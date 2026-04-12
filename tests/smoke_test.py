import subprocess
import sys


def main() -> None:
    import skill_trivium

    result = subprocess.run(
        [sys.executable, "-m", "skill_trivium", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise SystemExit(result.returncode)

    if "Commands" not in result.stdout:
        raise SystemExit("smoke test failed: missing CLI help output")

    if not hasattr(skill_trivium, "__version__"):
        raise SystemExit("smoke test failed: package import did not resolve")


if __name__ == "__main__":
    main()
