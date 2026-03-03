"""
Storage service abstraction for S3/MinIO/Local storage.
"""

import os
import re
from abc import ABC, abstractmethod
from typing import BinaryIO, Optional
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config


def get_global_bucket_name() -> str:
    """
    Get the name of the global bucket for shared scene storage.
    
    Returns:
        Global bucket name: vegetation-prime-global
    """
    return 'vegetation-prime-global'


def generate_tenant_bucket_name(tenant_id: str) -> str:
    """
    Generate a secure bucket name based on tenant_id.
    
    Bucket names must:
    - Be 3-63 characters long
    - Contain only lowercase letters, numbers, dots, and hyphens
    - Start and end with a letter or number
    - Not contain consecutive dots
    
    Args:
        tenant_id: Tenant identifier
        
    Returns:
        Sanitized bucket name: vegetation-prime-{sanitized_tenant_id}
    """
    # Sanitize tenant_id: remove special chars, convert to lowercase
    sanitized = re.sub(r'[^a-z0-9-]', '', tenant_id.lower())
    # Remove consecutive hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    # Ensure it's not empty
    if not sanitized:
        sanitized = 'default'
    
    # Generate bucket name
    bucket_name = f"vegetation-prime-{sanitized}"
    
    # Ensure length constraints (S3 bucket names: 3-63 chars)
    if len(bucket_name) > 63:
        # Truncate and add hash suffix
        hash_suffix = str(abs(hash(tenant_id)))[:8]
        bucket_name = f"veg-{sanitized[:50]}-{hash_suffix}"
    
    # Ensure minimum length
    if len(bucket_name) < 3:
        bucket_name = f"veg-{sanitized}"
    
    return bucket_name


class StorageService(ABC):
    """Abstract storage service interface."""
    
    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str, bucket: Optional[str] = None) -> str:
        """Upload a file to storage.
        
        Args:
            local_path: Local file path
            remote_path: Remote storage path
            bucket: Optional bucket name (uses default if not provided)
            
        Returns:
            Full URL/path to uploaded file
        """
        pass
    
    @abstractmethod
    def download_file(self, remote_path: str, local_path: str, bucket: Optional[str] = None) -> str:
        """Download a file from storage.
        
        Args:
            remote_path: Remote storage path
            local_path: Local file path to save to
            bucket: Optional bucket name
            
        Returns:
            Local file path
        """
        pass
    
    @abstractmethod
    def delete_file(self, remote_path: str, bucket: Optional[str] = None) -> bool:
        """Delete a file from storage.
        
        Args:
            remote_path: Remote storage path
            bucket: Optional bucket name
            
        Returns:
            True if deleted successfully
        """
        pass
    
    @abstractmethod
    def file_exists(self, remote_path: str, bucket: Optional[str] = None) -> bool:
        """Check if file exists in storage.
        
        Args:
            remote_path: Remote storage path
            bucket: Optional bucket name
            
        Returns:
            True if file exists
        """
        pass
    
    @abstractmethod
    def get_file_url(self, remote_path: str, bucket: Optional[str] = None, expires_in: int = 3600) -> str:
        """Get a presigned URL for file access.
        
        Args:
            remote_path: Remote storage path
            bucket: Optional bucket name
            expires_in: URL expiration time in seconds
            
        Returns:
            Presigned URL
        """
        pass
    
    @abstractmethod
    def copy_file(self, source_path: str, dest_path: str, source_bucket: Optional[str] = None, dest_bucket: Optional[str] = None) -> str:
        """Copy a file from one location to another (can be same or different buckets).
        
        Args:
            source_path: Source file path
            dest_path: Destination file path
            source_bucket: Optional source bucket name
            dest_bucket: Optional destination bucket name
            
        Returns:
            Full URL/path to copied file
        """
        pass


class S3StorageService(StorageService):
    """S3-compatible storage service (AWS S3 or MinIO)."""
    
    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        region: str = 'us-east-1',
        default_bucket: Optional[str] = None,
        use_ssl: bool = True
    ):
        """Initialize S3 storage service.
        
        Args:
            endpoint_url: S3 endpoint URL (None for AWS, MinIO URL for MinIO)
            access_key_id: Access key ID
            secret_access_key: Secret access key
            region: AWS region
            default_bucket: Default bucket name
            use_ssl: Use SSL for connections
        """
        self.default_bucket = default_bucket
        
        # Configure boto3 client
        config = Config(
            signature_version='s3v4',
            retries={'max_attempts': 3, 'mode': 'standard'}
        )
        
        resolved_endpoint = endpoint_url or os.getenv('S3_ENDPOINT_URL')
        resolved_access_key = access_key_id or os.getenv('AWS_ACCESS_KEY_ID')
        resolved_secret_key = secret_access_key or os.getenv('AWS_SECRET_ACCESS_KEY')

        self.client = boto3.client(
            's3',
            endpoint_url=resolved_endpoint,
            aws_access_key_id=resolved_access_key,
            aws_secret_access_key=resolved_secret_key,
            region_name=region,
            use_ssl=use_ssl,
            config=config
        )

        self.resource = boto3.resource(
            's3',
            endpoint_url=resolved_endpoint,
            aws_access_key_id=resolved_access_key,
            aws_secret_access_key=resolved_secret_key,
            region_name=region,
            use_ssl=use_ssl,
            config=config
        )
    
    def _get_bucket(self, bucket: Optional[str] = None) -> str:
        """Get bucket name, using default if not provided."""
        return bucket or self.default_bucket or os.getenv('S3_BUCKET', 'vegetation-prime')
    
    def upload_file(self, local_path: str, remote_path: str, bucket: Optional[str] = None) -> str:
        """Upload file to S3."""
        bucket_name = self._get_bucket(bucket)
        
        try:
            # Ensure bucket exists
            self._ensure_bucket_exists(bucket_name)
            
            # Upload file
            self.client.upload_file(
                local_path,
                bucket_name,
                remote_path,
                ExtraArgs={'ContentType': self._get_content_type(remote_path)}
            )
            
            return f"s3://{bucket_name}/{remote_path}"
        except ClientError as e:
            raise Exception(f"Failed to upload file to S3: {str(e)}")
    
    def download_file(self, remote_path: str, local_path: str, bucket: Optional[str] = None) -> str:
        """Download file from S3."""
        bucket_name = self._get_bucket(bucket)
        
        try:
            # Ensure local directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Download file
            self.client.download_file(bucket_name, remote_path, local_path)
            
            return local_path
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise FileNotFoundError(f"File not found: {remote_path}")
            raise Exception(f"Failed to download file from S3: {str(e)}")
    
    def delete_file(self, remote_path: str, bucket: Optional[str] = None) -> bool:
        """Delete file from S3."""
        bucket_name = self._get_bucket(bucket)
        
        try:
            self.client.delete_object(Bucket=bucket_name, Key=remote_path)
            return True
        except ClientError as e:
            raise Exception(f"Failed to delete file from S3: {str(e)}")
    
    def file_exists(self, remote_path: str, bucket: Optional[str] = None) -> bool:
        """Check if file exists in S3."""
        bucket_name = self._get_bucket(bucket)
        
        try:
            self.client.head_object(Bucket=bucket_name, Key=remote_path)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise Exception(f"Failed to check file existence in S3: {str(e)}")
    
    def get_file_url(self, remote_path: str, bucket: Optional[str] = None, expires_in: int = 3600) -> str:
        """Get presigned URL for file access."""
        bucket_name = self._get_bucket(bucket)
        
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': remote_path},
                ExpiresIn=expires_in
            )
            return url
        except ClientError as e:
            raise Exception(f"Failed to generate presigned URL: {str(e)}")
    
    def _ensure_bucket_exists(self, bucket_name: str) -> None:
        """Ensure bucket exists, create if it doesn't."""
        try:
            self.client.head_bucket(Bucket=bucket_name)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                # Bucket doesn't exist, create it
                self.client.create_bucket(Bucket=bucket_name)
            else:
                raise
    
    def copy_file(self, source_path: str, dest_path: str, source_bucket: Optional[str] = None, dest_bucket: Optional[str] = None) -> str:
        """Copy file from source bucket to destination bucket (S3 copy operation)."""
        source_bucket_name = self._get_bucket(source_bucket)
        dest_bucket_name = self._get_bucket(dest_bucket)
        
        try:
            # Ensure destination bucket exists
            self._ensure_bucket_exists(dest_bucket_name)
            
            # Use S3 copy operation (server-side, no download/upload)
            copy_source = {
                'Bucket': source_bucket_name,
                'Key': source_path
            }
            
            self.client.copy_object(
                CopySource=copy_source,
                Bucket=dest_bucket_name,
                Key=dest_path,
                ContentType=self._get_content_type(dest_path)
            )
            
            return f"s3://{dest_bucket_name}/{dest_path}"
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise FileNotFoundError(f"Source file not found: {source_path}")
            raise Exception(f"Failed to copy file in S3: {str(e)}")
    
    def _get_content_type(self, file_path: str) -> str:
        """Get content type based on file extension."""
        ext = Path(file_path).suffix.lower()
        content_types = {
            '.tif': 'image/tiff',
            '.tiff': 'image/tiff',
            '.cog': 'image/tiff',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.json': 'application/json',
            '.geojson': 'application/geo+json',
        }
        return content_types.get(ext, 'application/octet-stream')


class LocalStorageService(StorageService):
    """Local filesystem storage service."""
    
    def __init__(self, base_path: str = '/tmp/vegetation-prime'):
        """Initialize local storage service.
        
        Args:
            base_path: Base directory for storage
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def upload_file(self, local_path: str, remote_path: str, bucket: Optional[str] = None) -> str:
        """Copy file to local storage."""
        remote_full_path = self.base_path / (bucket or '') / remote_path
        remote_full_path.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(local_path, remote_full_path)
        
        return str(remote_full_path)
    
    def download_file(self, remote_path: str, local_path: str, bucket: Optional[str] = None) -> str:
        """Copy file from local storage."""
        remote_full_path = self.base_path / (bucket or '') / remote_path
        
        if not remote_full_path.exists():
            raise FileNotFoundError(f"File not found: {remote_path}")
        
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(remote_full_path, local_path)
        
        return local_path
    
    def delete_file(self, remote_path: str, bucket: Optional[str] = None) -> bool:
        """Delete file from local storage."""
        remote_full_path = self.base_path / (bucket or '') / remote_path
        
        if remote_full_path.exists():
            remote_full_path.unlink()
            return True
        return False
    
    def file_exists(self, remote_path: str, bucket: Optional[str] = None) -> bool:
        """Check if file exists in local storage."""
        remote_full_path = self.base_path / (bucket or '') / remote_path
        return remote_full_path.exists()
    
    def copy_file(self, source_path: str, dest_path: str, source_bucket: Optional[str] = None, dest_bucket: Optional[str] = None) -> str:
        """Copy file from source location to destination (local filesystem)."""
        source_full_path = self.base_path / (source_bucket or '') / source_path
        dest_full_path = self.base_path / (dest_bucket or '') / dest_path
        
        if not source_full_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        
        dest_full_path.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(source_full_path, dest_full_path)
        
        return str(dest_full_path)
    
    def get_file_url(self, remote_path: str, bucket: Optional[str] = None, expires_in: int = 3600) -> str:
        """Get file path (no presigning for local storage)."""
        remote_full_path = self.base_path / (bucket or '') / remote_path
        return str(remote_full_path)


def create_storage_service(
    storage_type: str,
    endpoint_url: Optional[str] = None,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    default_bucket: Optional[str] = None,
    base_path: Optional[str] = None
) -> StorageService:
    """Factory function to create storage service.
    
    Args:
        storage_type: 's3', 'minio', or 'local'
        endpoint_url: Endpoint URL (for MinIO)
        access_key_id: Access key ID
        secret_access_key: Secret access key
        default_bucket: Default bucket name
        base_path: Base path for local storage
        
    Returns:
        StorageService instance
    """
    if storage_type in ('s3', 'minio'):
        use_ssl = storage_type == 's3'  # MinIO might not use SSL in dev
        return S3StorageService(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            default_bucket=default_bucket,
            use_ssl=use_ssl
        )
    elif storage_type == 'local':
        return LocalStorageService(base_path=base_path or '/tmp/vegetation-prime')
    else:
        raise ValueError(f"Unsupported storage type: {storage_type}")

