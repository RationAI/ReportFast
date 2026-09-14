"""Data Resolvers: Decouple data fetching from rendering."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass
import json
import os


@dataclass
class ResolutionContext:
    """Context passed to resolvers for metadata about the current request."""
    slide_id: Optional[str] = None
    run_id: Optional[str] = None
    user_id: Optional[str] = None
    extra: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.extra is None:
            self.extra = {}


class Resolver(ABC):
    """Abstract base class for data resolvers.
    
    Resolvers fetch data from various sources (local filesystem, S3, MLflow)
    and convert it into a standard Python dict/list structure that components
    can consume without knowing the data's origin.
    """
    
    def __init__(self, source: str, cache_enabled: bool = True):
        self.source = source
        self._cache: Dict[str, Any] = {} if cache_enabled else None
        self.cache_enabled = cache_enabled
    
    def _cache_key(self, **kwargs) -> str:
        """Generate a cache key from fetch parameters."""
        return json.dumps(kwargs, sort_keys=True)
    
    def fetch(self, context: Optional[ResolutionContext] = None, **kwargs) -> Any:
        """Fetch data with optional caching."""
        cache_key = self._cache_key(**kwargs)
        
        if self.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]
        
        data = self._fetch_impl(context=context, **kwargs)
        
        if self.cache_enabled:
            self._cache[cache_key] = data
        
        return data
    
    @abstractmethod
    def _fetch_impl(self, context: Optional[ResolutionContext] = None, **kwargs) -> Any:
        """Actual fetch implementation. Override in subclasses."""
        pass
    
    def clear_cache(self):
        """Clear the resolver's cache."""
        if self._cache is not None:
            self._cache.clear()


class LocalResolver(Resolver):
    """Resolver for local filesystem data (e.g., /mnt/data)."""
    
    def __init__(self, base_path: str = "/mnt/data", **kwargs):
        super().__init__(source=base_path, **kwargs)
        self.base_path = base_path
    
    def _fetch_impl(self, context: Optional[ResolutionContext] = None, 
                    path: str = "", slide_id: Optional[str] = None,
                    load_tiff_meta: bool = True, **kwargs) -> Dict[str, Any]:
        """Fetch metadata and thumbnail paths for a local slide.
        
        For Big Data compatibility, we only load metadata and thumbnail paths,
        never the full WSI pyramid.
        """
        import openslide
        from pathlib import Path
        
        # Resolve the slide path
        if slide_id and not path:
            path = os.path.join(self.base_path, f"{slide_id}.svs")
            # Try other extensions if .svs doesn't exist
            for ext in [".tiff", ".tif", ".mrxs"]:
                alt_path = os.path.join(self.base_path, f"{slide_id}{ext}")
                if os.path.exists(alt_path):
                    path = alt_path
                    break
        
        full_path = os.path.join(self.base_path, path) if not os.path.isabs(path) else path
        
        if not os.path.exists(full_path):
            return {"error": f"Slide not found: {full_path}", "path": full_path}
        
        result = {
            "path": full_path,
            "slide_id": slide_id or Path(path).stem,
            "format": Path(path).suffix.lower().lstrip("."),
            "exists": True,
        }
        
        # Extract metadata using OpenSlide (lightweight - only reads headers)
        if load_tiff_meta:
            try:
                with openslide.OpenSlide(full_path) as slide:
                    props = dict(slide.properties)
                    result["dimensions"] = slide.dimensions
                    result["level_count"] = slide.level_count
                    result["level_dimensions"] = slide.level_dimensions
                    result["properties"] = props
                    result["mpp_x"] = float(props.get(openslide.PROPERTY_NAME_MPP_X, 0))
                    result["mpp_y"] = float(props.get(openslide.PROPERTY_NAME_MPP_Y, 0))
                    result["vendor"] = props.get(openslide.PROPERTY_NAME_VENDOR, "unknown")
                    
                    # Generate thumbnail path (we create it on first access)
                    thumb_path = f"{full_path}.thumb.jpg"
                    if not os.path.exists(thumb_path):
                        thumb = slide.get_thumbnail((512, 512))
                        thumb.save(thumb_path)
                    result["thumbnail_path"] = thumb_path
            except Exception as e:
                result["metadata_error"] = str(e)
        
        return result


class S3Resolver(Resolver):
    """Resolver for S3-hosted slide data.
    
    Instead of downloading full slides, this resolver generates
    pre-signed URLs for viewers and fetches metadata JSON.
    """
    
    def __init__(self, bucket: str, prefix: str = "slides/", 
                 endpoint_url: Optional[str] = None, **kwargs):
        super().__init__(source=f"s3://{bucket}/{prefix}", **kwargs)
        self.bucket = bucket
        self.prefix = prefix
        self.endpoint_url = endpoint_url
        self._client = None
    
    def _get_client(self):
        import boto3
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url
            )
        return self._client
    
    def _fetch_impl(self, context: Optional[ResolutionContext] = None,
                    slide_id: str = "", **kwargs) -> Dict[str, Any]:
        import boto3
        from botocore.exceptions import ClientError
        
        client = self._get_client()
        key = f"{self.prefix}{slide_id}"
        
        # List objects to find the actual file with extension
        try:
            response = client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=key,
                MaxKeys=5
            )
            
            objects = response.get("Contents", [])
            if not objects:
                return {"error": f"No objects found for {key}"}
            
            # Find the main slide file
            slide_obj = None
            for obj in objects:
                ext = obj["Key"].split(".")[-1].lower()
                if ext in ["svs", "tiff", "tif", "mrxs"]:
                    slide_obj = obj
                    break
            
            if not slide_obj:
                slide_obj = objects[0]
            
            actual_key = slide_obj["Key"]
            
            # Generate pre-signed URL for the viewer
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": actual_key},
                ExpiresIn=3600
            )
            
            # Try to fetch metadata JSON if it exists
            meta_key = f"{actual_key}.meta.json"
            metadata = {}
            try:
                meta_obj = client.get_object(Bucket=self.bucket, Key=meta_key)
                metadata = json.loads(meta_obj["Body"].read())
            except ClientError:
                pass
            
            return {
                "slide_id": slide_id,
                "s3_key": actual_key,
                "presigned_url": url,
                "format": actual_key.split(".")[-1].lower(),
                "size_mb": slide_obj["Size"] / (1024 * 1024),
                "metadata": metadata,
                "source": "s3",
            }
            
        except Exception as e:
            return {"error": str(e)}


class MLflowResolver(Resolver):
    """Resolver for MLflow experiment metrics and artifacts.
    
    Fetches metrics, parameters, and artifact URIs from MLflow tracking.
    """
    
    def __init__(self, tracking_uri: str = "http://localhost:5000",
                 experiment_id: Optional[str] = None, **kwargs):
        super().__init__(source=tracking_uri, **kwargs)
        self.tracking_uri = tracking_uri
        self.experiment_id = experiment_id
    
    def _get_client(self):
        import mlflow
        mlflow.set_tracking_uri(self.tracking_uri)
        return mlflow
    
    def _fetch_impl(self, context: Optional[ResolutionContext] = None,
                    run_id: Optional[str] = None, 
                    experiment_id: Optional[str] = None,
                    metric_names: Optional[list] = None, **kwargs) -> Dict[str, Any]:
        
        mlflow = self._get_client()
        exp_id = experiment_id or self.experiment_id
        
        result = {
            "tracking_uri": self.tracking_uri,
            "experiment_id": exp_id,
        }
        
        try:
            if run_id:
                # Fetch specific run data
                run = mlflow.get_run(run_id)
                result["run"] = {
                    "run_id": run.info.run_id,
                    "status": run.info.status,
                    "start_time": run.info.start_time,
                    "params": dict(run.data.params),
                    "metrics": dict(run.data.metrics),
                }
                
                # Fetch metric history if specific metrics requested
                if metric_names:
                    history = {}
                    for metric_name in metric_names:
                        try:
                            metric_history = mlflow.get_metric_history(run_id, metric_name)
                            history[metric_name] = [
                                {"step": m.step, "value": m.value, "timestamp": m.timestamp}
                                for m in metric_history
                            ]
                        except Exception:
                            history[metric_name] = []
                    result["run"]["metric_history"] = history
                
            else:
                # List runs for experiment
                runs = mlflow.search_runs(
                    experiment_ids=[exp_id] if exp_id else None,
                    output_format="list"
                )
                result["runs"] = [
                    {
                        "run_id": r.info.run_id,
                        "status": r.info.status,
                        "params": dict(r.data.params),
                        "metrics": dict(r.data.metrics),
                    }
                    for r in runs[:50]  # Limit for performance
                ]
                result["total_runs"] = len(runs)
                
        except Exception as e:
            result["error"] = str(e)
        
        return result
