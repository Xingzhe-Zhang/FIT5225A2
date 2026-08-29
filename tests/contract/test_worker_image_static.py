from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_image_avoids_megadetector_protobuf_resolver_conflict() -> None:
    dockerfile = (ROOT / "deployment" / "aws-worker" / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "deployment" / "aws-worker" / "requirements-worker-container.txt").read_text(
        encoding="utf-8"
    )

    assert '"yolov5==7.0.14"' in dockerfile
    assert '--no-deps "megadetector==5.0.4"' in dockerfile
    assert "megadetector" not in requirements.casefold()
    assert "onnx==1.16.2" in requirements
    for dependency in ("humanfriendly", "jsonpickle", "pyqtree", "scikit-learn", "seaborn"):
        assert dependency in requirements
    assert "python -m pip check" in dockerfile


def test_worker_push_script_refreshes_ecr_authentication() -> None:
    script = (ROOT / "scripts" / "project_tasks.py").read_text(encoding="utf-8")

    assert '"sts", "get-caller-identity"' in script
    assert '"ecr", "describe-repositories"' in script
    assert '"ecr", "get-login-password"' in script
    assert '"login", "--username", "AWS", "--password-stdin"' in script
