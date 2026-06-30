"""Autonomous cloud environment detection for the ComputeCapX SDK."""

import os
import requests
import platform
from typing import Dict, Optional

class EnvironmentDetector:
    """
    Identifies the underlying compute environment by querying standard cloud provider 
    metadata endpoints and environment signatures.
    Executes with extremely low timeouts to prevent latency in local or unsupported environments.
    """
    
    # Short timeout prevents latency in non-cloud or unsupported environments.
    METADATA_TIMEOUT = 1.0  
    _cached_env = None

    @staticmethod
    def _ping_aws() -> Optional[Dict[str, str]]:
        try:
            token_res = requests.put(
                "http://169.254.169.254/latest/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
                timeout=EnvironmentDetector.METADATA_TIMEOUT
            )
            
            headers = {}
            if token_res.status_code == 200:
                headers["X-aws-ec2-metadata-token"] = token_res.text

            res = requests.get(
                "http://169.254.169.254/latest/meta-data/instance-id", 
                headers=headers,
                timeout=EnvironmentDetector.METADATA_TIMEOUT
            )
            
            if res.status_code == 200 and res.text.startswith("i-"):
                region, instance_type = "us-east-1", "unknown"
                try:
                    reg_res = requests.get("http://169.254.169.254/latest/meta-data/placement/region", headers=headers, timeout=EnvironmentDetector.METADATA_TIMEOUT)
                    if reg_res.status_code == 200:
                        region = reg_res.text
                        
                    type_res = requests.get("http://169.254.169.254/latest/meta-data/instance-type", headers=headers, timeout=EnvironmentDetector.METADATA_TIMEOUT)
                    if type_res.status_code == 200:
                        instance_type = type_res.text
                except Exception:
                    pass
                return {"resource_id": res.text, "region": region, "instance_type": instance_type}
        except requests.exceptions.RequestException:
            pass
        return None

    @staticmethod
    def _ping_digitalocean() -> Optional[Dict[str, str]]:
        try:
            res = requests.get(
                "http://169.254.169.254/metadata/v1/id", 
                timeout=EnvironmentDetector.METADATA_TIMEOUT
            )
            if res.status_code == 200 and res.text.isdigit():
                region, instance_type = "nyc3", "unknown"
                try:
                    reg_res = requests.get("http://169.254.169.254/metadata/v1/region", timeout=EnvironmentDetector.METADATA_TIMEOUT)
                    if reg_res.status_code == 200:
                        region = reg_res.text
                        
                    size_res = requests.get("http://169.254.169.254/metadata/v1/size", timeout=EnvironmentDetector.METADATA_TIMEOUT)
                    if size_res.status_code == 200:
                        instance_type = size_res.text
                except Exception:
                    pass
                return {"resource_id": f"droplet-{res.text}", "region": region, "instance_type": instance_type}
        except requests.exceptions.RequestException:
            pass
        return None

    @staticmethod
    def _ping_gcp() -> Optional[Dict[str, str]]:
        try:
            headers = {"Metadata-Flavor": "Google"}
            res = requests.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/id", 
                headers=headers,
                timeout=EnvironmentDetector.METADATA_TIMEOUT
            )
            if res.status_code == 200:
                zone, instance_type = "us-central1-a", "unknown"
                try:
                    zone_res = requests.get("http://metadata.google.internal/computeMetadata/v1/instance/zone", headers=headers, timeout=EnvironmentDetector.METADATA_TIMEOUT)
                    if zone_res.status_code == 200:
                        zone = zone_res.text.split('/')[-1]
                        
                    type_res = requests.get("http://metadata.google.internal/computeMetadata/v1/instance/machine-type", headers=headers, timeout=EnvironmentDetector.METADATA_TIMEOUT)
                    if type_res.status_code == 200:
                        instance_type = type_res.text.split('/')[-1]
                except Exception:
                    pass
                return {"resource_id": res.text, "region": zone, "instance_type": instance_type}
        except requests.exceptions.RequestException:
            pass
        return None

    @staticmethod
    def _ping_azure() -> Optional[Dict[str, str]]:
        try:
            res = requests.get(
                "http://169.254.169.254/metadata/instance?api-version=2021-02-01", 
                headers={"Metadata": "true"},
                timeout=EnvironmentDetector.METADATA_TIMEOUT
            )
            if res.status_code == 200:
                data = res.json().get('compute', {})
                r_id = data.get('resourceId', 'azure-instance')
                # Resource Group Name is used as the region identifier for Azure resources.
                r_group = data.get('resourceGroupName', 'unknown-group')
                instance_type = data.get('vmSize', 'unknown')
                
                return {"resource_id": r_id, "region": r_group, "instance_type": instance_type}
        except Exception:
            pass
        return None

    @staticmethod
    def _ping_oci() -> Optional[Dict[str, str]]:
        """Detects Oracle Cloud Infrastructure (OCI) via the OPCv2 metadata endpoint."""
        try:
            # OCI requires the 'Authorization: Bearer Oracle' header for metadata v2
            headers = {"Authorization": "Bearer Oracle"}
            res = requests.get(
                "http://169.254.169.254/opc/v2/instance/", 
                headers=headers,
                timeout=EnvironmentDetector.METADATA_TIMEOUT
            )
            if res.status_code == 200:
                data = res.json()
                r_id = data.get("id", "oci-instance")
                region = data.get("region", "unknown-region")
                instance_type = data.get("shape", "unknown")
                
                return {"resource_id": r_id, "region": region, "instance_type": instance_type}
        except Exception:
            pass
        return None

    @staticmethod
    def _check_edge() -> Optional[Dict[str, str]]:
        """Detects serverless and edge environments via environment variables."""
        if os.getenv("VERCEL"):
            return {"provider": "vercel", "resource_id": os.getenv("VERCEL_GIT_REPO_SLUG", "vercel-app")}
        if os.getenv("NETLIFY"):
            return {"provider": "netlify", "resource_id": os.getenv("SITE_NAME", "netlify-app")}
        return None

    @classmethod
    def detect_environment(cls) -> Dict[str, str]:
        """
        Executes a rapid cascade of metadata checks.
        Returns a dictionary containing the provider, unique resource identifier, region, and instance type.
        """
        if cls._cached_env is not None:
            return cls._cached_env

        def _cache_and_return(res):
            cls._cached_env = res
            return res

        # 1. Check Edge/Serverless first (Environment variables are instant)
        edge = cls._check_edge()
        if edge:
            edge["region"] = "global"
            edge["instance_type"] = "serverless"
            return _cache_and_return(edge)

        # 2. Check environment override to bypass network checks entirely
        env_override = os.getenv("COMPUTECAPX_ENV")
        if env_override:
            env_override = env_override.lower()
            if env_override in ("local", "dev", "development"):
                node_name = platform.node() or "unknown-host"
                return _cache_and_return({"provider": "local", "resource_id": node_name, "region": "local", "instance_type": "local-machine"})
            elif env_override in ("aws", "gcp", "azure", "oci", "digitalocean"):
                node_name = platform.node() or "unknown-host"
                return _cache_and_return({"provider": env_override, "resource_id": node_name, "region": "override", "instance_type": "override"})

        # 3. Check standard Cloud Metadata IPs concurrently using daemon threads to prevent interpreter exit hangs
        import threading
        import queue

        check_fns = [
            ("aws", cls._ping_aws),
            ("gcp", cls._ping_gcp),
            ("azure", cls._ping_azure),
            ("oci", cls._ping_oci),
            ("digitalocean", cls._ping_digitalocean),
        ]

        results = queue.Queue()

        def worker(provider, fn):
            try:
                res = fn()
                if res:
                    results.put((provider, res))
            except Exception:
                pass

        for provider, fn in check_fns:
            t = threading.Thread(target=worker, args=(provider, fn), daemon=True)
            t.start()

        try:
            # Block at most METADATA_TIMEOUT for the first successful cloud metadata ping
            provider, res = results.get(timeout=cls.METADATA_TIMEOUT)
            return _cache_and_return({
                "provider": provider,
                "resource_id": res["resource_id"],
                "region": res["region"],
                "instance_type": res["instance_type"]
            })
        except queue.Empty:
            pass

        # 4. Fallback to Local/Container
        node_name = platform.node() or "unknown-host"
        return _cache_and_return({"provider": "local", "resource_id": node_name, "region": "local", "instance_type": "local-machine"})