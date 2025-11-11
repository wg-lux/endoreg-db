"""Utility to upload safetensor weights to Hugging Face."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a safetensor file to Hugging Face Hub")
    parser.add_argument(
        "--local-path",
        type=Path,
        default=Path("tests/assets/colo_segmentation_RegNetX800MF_6.safetensors"),
        help="Path to the local safetensor file",
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        default="wg-lux/colo_segmentation_RegNetX800MF_base",
        help="Target Hugging Face repository (owner/name)",
    )
    parser.add_argument(
        "--path-in-repo",
        type=str,
        default="colo_segmentation_RegNetX800MF_base.safetensors",
        help="Filename to use inside the repository",
    )
    parser.add_argument(
        "--commit-message",
        type=str,
        default="Add safetensor weights",
        help="Commit message for the upload",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Optional Hugging Face token; falls back to HF_TOKEN env var",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    token = args.token or os.getenv("HF_TOKEN")
    if not token:
        raise SystemExit("Hugging Face token not provided. Set HF_TOKEN or pass --token.")

    if not args.local_path.exists():
        raise SystemExit(f"Local file not found: {args.local_path}")

    api = HfApi()
    api.upload_file(
        path_or_fileobj=str(args.local_path),
        path_in_repo=args.path_in_repo,
        repo_id=args.repo_id,
        repo_type="model",
        token=token,
        commit_message=args.commit_message,
    )
    print(f"Uploaded {args.local_path} to {args.repo_id}:{args.path_in_repo}")


if __name__ == "__main__":
    main()
