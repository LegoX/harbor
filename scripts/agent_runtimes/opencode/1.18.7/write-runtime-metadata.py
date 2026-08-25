import json
import os
import subprocess
from pathlib import Path

runtime_root = Path(os.environ["RUNTIME_ROOT"])
package_name = os.environ["PACKAGE_NAME"]
package_version = os.environ["PACKAGE_VERSION"]
npm_version = os.environ["NPM_VERSION"]
node_abis = os.environ["NODE_ABIS"].split()
binary_name = os.environ["BINARY_NAME"]

metadata = {
    "runtime_mode": "mounted-node-cli",
    "opencode_executable": str(runtime_root / "bin" / binary_name),
    "node_executable": str(runtime_root / "bin" / "node"),
    "node_abis": node_abis,
    "node_homes": {abi: str(runtime_root / "node-home" / abi) for abi in node_abis},
    "opencode_binaries": {
        abi: str(runtime_root / "opencode-home" / abi / "bin" / binary_name)
        for abi in node_abis
    },
    "runtime_env_script": str(runtime_root / "runtime-env.sh"),
    "packages": {
        "npm": npm_version,
        package_name: package_version,
    },
}

version_result = subprocess.run(
    [str(runtime_root / "bin" / binary_name), "--version"],
    check=True,
    capture_output=True,
    text=True,
    env={
        **os.environ,
        "CUSTOM_AGENT_RUNTIME_ROOT": str(runtime_root),
        "CUSTOM_AGENT_RUNTIME_NODE_ABI": "glibc",
        "PATH": f"{runtime_root / 'bin'}:{runtime_root / 'node-home' / 'glibc' / 'bin'}:{os.environ.get('PATH', '')}",
    },
)
metadata["opencode_version_output"] = version_result.stdout.strip()

(runtime_root / "runtime-metadata.json").write_text(json.dumps(metadata, indent=2))
