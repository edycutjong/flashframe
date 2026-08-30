import os
import json
import subprocess
from huggingface_hub import HfApi

def deploy():
    api = HfApi()
    repo_id = "edycutjong/flashframe"
    
    print(f"Creating space {repo_id}...")
    try:
        api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker", private=False)
    except Exception as e:
        print(f"Space might already exist (or error): {e}")

    print("Setting secrets...")
    # Clickhouse secrets
    ch_env = os.path.expanduser("~/.config/flashframe/clickhouse.env")
    if os.path.exists(ch_env):
        with open(ch_env) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line:
                    key, val = line.split("=", 1)
                    api.add_space_secret(repo_id, key, val)
    
    # Gemini secrets
    gem_json = os.path.expanduser("~/.config/gemini/credentials.json")
    if os.path.exists(gem_json):
        with open(gem_json) as f:
            creds = json.load(f)
            api.add_space_secret(repo_id, "GEMINI_API_KEY", creds["keys"][0]["key"])
            api.add_space_secret(repo_id, "GEMINI_MODEL", creds["model"])

    print("Secrets configured.")
    print("Uploading to Hugging Face...")
    # We will upload the current directory via HfApi
    # This avoids Git remote complexity and respects .gitignore / .dockerignore implicitly, but upload_folder is safer if we just explicitly allow patterns.
    # Actually, the simplest is standard git push. But let's just use git push from the bash script.

if __name__ == "__main__":
    deploy()
