from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


ROOT = (
    Path(sys.executable).resolve().parent.parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
DEFAULT_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DAILY_PHOTO_NAME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})\.(?:jpe?g|png)$",
    re.IGNORECASE,
)


@dataclass
class FrameResult:
    source: str
    aligned: str | None
    status: str
    detail: str = ""


def resource_path(relative: Path) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", ROOT))
    bundled = bundle_root / relative
    return bundled if bundled.exists() else ROOT / relative


def load_project_config() -> dict:
    path = ROOT / "config.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def configured_photos_root() -> Path:
    configured = Path(str(load_project_config().get("photos_directory", "photos"))).expanduser()
    return configured if configured.is_absolute() else ROOT / configured


def find_images(input_root: Path, include_all: bool = False) -> list[Path]:
    return sorted(
        path
        for path in input_root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        and (include_all or DAILY_PHOTO_NAME.match(path.name))
    )


def choose_face(faces: np.ndarray, width: int, height: int) -> np.ndarray:
    """Prefer the large face nearest the image center."""
    center = np.array([width / 2.0, height / 2.0])

    def rank(face: np.ndarray) -> float:
        x, y, w, h = face[:4]
        face_center = np.array([x + w / 2.0, y + h / 2.0])
        distance = np.linalg.norm((face_center - center) / np.array([width, height]))
        return float(w * h) / (1.0 + distance)

    return max(faces, key=rank)


def output_dimensions(size: int, crop: str) -> tuple[int, int]:
    if crop == "face":
        return size, size
    height = round(size * 9 / 16)
    return size, height - height % 2


def alignment_matrix(face: np.ndarray, output_size: tuple[int, int], crop: str) -> np.ndarray:
    width, height = output_size
    landmarks = face[4:14].reshape(5, 2).astype(np.float32)
    eyes = landmarks[:2][np.argsort(landmarks[:2, 0])]
    mouths = landmarks[3:5][np.argsort(landmarks[3:5, 0])]
    source = np.vstack((eyes, mouths)).astype(np.float32)
    if crop == "face":
        target = np.array(
            [
                [0.34 * width, 0.38 * height],
                [0.66 * width, 0.38 * height],
                [0.41 * width, 0.66 * height],
                [0.59 * width, 0.66 * height],
            ],
            dtype=np.float32,
        )
    else:
        target = np.array(
            [
                [0.425 * width, 0.40 * height],
                [0.575 * width, 0.40 * height],
                [0.458 * width, 0.67 * height],
                [0.542 * width, 0.67 * height],
            ],
            dtype=np.float32,
        )
    matrix, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
    if matrix is None:
        raise ValueError("无法根据人脸特征点计算对齐变换")
    return matrix


def align_image(
    image: np.ndarray,
    detector: cv2.FaceDetectorYN,
    output_size: tuple[int, int],
    crop: str,
    background_blur: int,
) -> np.ndarray:
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, detected = detector.detect(image)
    if detected is None or len(detected) == 0:
        raise ValueError("没有检测到人脸")
    face = choose_face(detected, width, height)
    matrix = alignment_matrix(face, output_size, crop)
    if background_blur > 0:
        kernel = background_blur if background_blur % 2 == 1 else background_blur + 1
        background = cv2.GaussianBlur(image, (kernel, kernel), 0)
        canvas = cv2.warpAffine(
            background,
            matrix,
            output_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        return cv2.warpAffine(
            image,
            matrix,
            output_size,
            dst=canvas,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_TRANSPARENT,
        )
    return cv2.warpAffine(
        image,
        matrix,
        output_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )


def save_gif(frame_paths: list[Path], destination: Path, duration_ms: int) -> None:
    frames: list[Image.Image] = []
    try:
        for path in frame_paths:
            with Image.open(path) as image:
                frames.append(image.convert("RGB").quantize(colors=256))
        if not frames:
            raise ValueError("没有可用于生成 GIF 的对齐帧")
        temporary = destination.with_name(destination.name + ".tmp")
        frames[0].save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=False,
            disposal=2,
        )
        os.replace(temporary, destination)
    finally:
        for frame in frames:
            frame.close()


def save_mp4(
    frame_paths: list[Path], destination: Path, fps: float, output_size: tuple[int, int]
) -> None:
    temporary = destination.with_name(destination.stem + ".tmp" + destination.suffix)
    writer = cv2.VideoWriter(
        str(temporary), cv2.VideoWriter_fourcc(*"mp4v"), fps, output_size
    )
    if not writer.isOpened():
        raise OSError("无法初始化 MP4 编码器")
    try:
        for path in frame_paths:
            frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if frame is None:
                raise OSError(f"无法读取对齐帧：{path}")
            writer.write(frame)
    finally:
        writer.release()
    os.replace(temporary, destination)


def frame_timestamp(path: Path, mode: str) -> str:
    if mode == "none":
        return ""
    match = DAILY_PHOTO_NAME.match(path.name)
    if match is None:
        return path.stem if mode == "datetime" else ""
    date, hour, minute, second = match.groups()
    return date if mode == "date" else f"{date} {hour}:{minute}:{second}"


def draw_timestamp(image: np.ndarray, label: str) -> None:
    if not label:
        return
    height, width = image.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.55, width / 1100.0)
    thickness = max(1, round(width / 500))
    (text_width, text_height), baseline = cv2.getTextSize(label, font, scale, thickness)
    padding = max(8, round(width / 80))
    left = padding
    bottom = height - padding
    top = bottom - text_height - baseline - padding
    right = left + text_width + padding * 2
    overlay = image.copy()
    cv2.rectangle(overlay, (left, top), (right, bottom), (0, 0, 0), cv2.FILLED)
    cv2.addWeighted(overlay, 0.58, image, 0.42, 0, image)
    cv2.putText(
        image,
        label,
        (left + padding, bottom - baseline - padding // 2),
        font,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def create_timelapse(args: argparse.Namespace, progress=None) -> tuple[list[Path], list[FrameResult]]:
    input_root = args.input.resolve()
    output_root = args.output.resolve()
    aligned_root = output_root / "aligned"
    gif_path = output_root / args.gif_name
    video_path = output_root / args.video_name
    dimensions = output_dimensions(args.size, args.crop)
    images = find_images(input_root, args.include_all)
    if not images:
        raise ValueError(f"没有在 {input_root} 中找到符合条件的照片")
    if not args.model.exists():
        raise FileNotFoundError(f"找不到人脸检测模型：{args.model}")

    detector = cv2.FaceDetectorYN.create(
        str(args.model), "", (320, 320), args.confidence, 0.3, 5000
    )
    aligned_root.mkdir(parents=True, exist_ok=True)
    results: list[FrameResult] = []
    aligned_paths: list[Path] = []

    for index, source in enumerate(images, start=1):
        relative = source.relative_to(input_root)
        destination = (aligned_root / relative).with_suffix(".jpg")
        if progress is None:
            print(f"[{index}/{len(images)}] {relative}", flush=True)
        else:
            progress(index, len(images), relative)
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            results.append(FrameResult(str(relative), None, "skipped", "无法读取图片"))
            continue
        try:
            aligned = align_image(
                image, detector, dimensions, args.crop, args.background_blur
            )
        except (ValueError, cv2.error) as exc:
            results.append(FrameResult(str(relative), None, "skipped", str(exc)))
            continue
        draw_timestamp(aligned, frame_timestamp(source, args.timestamp))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(destination), aligned, [cv2.IMWRITE_JPEG_QUALITY, args.quality]):
            results.append(FrameResult(str(relative), None, "skipped", "无法写入对齐图片"))
            continue
        aligned_paths.append(destination)
        results.append(
            FrameResult(str(relative), str(destination.relative_to(output_root)), "aligned")
        )

    skipped = [result for result in results if result.status != "aligned"]
    if args.strict and skipped:
        raise ValueError(f"有 {len(skipped)} 张照片无法对齐；严格模式下不生成输出")
    generated: list[Path] = []
    if args.format in {"gif", "both"}:
        save_gif(aligned_paths, gif_path, args.duration)
        generated.append(gif_path)
    if args.format in {"mp4", "both"}:
        save_mp4(aligned_paths, video_path, 1000.0 / args.duration, dimensions)
        generated.append(video_path)
    manifest = {
        "input": str(input_root),
        "outputs": [str(path) for path in generated],
        "frames": len(aligned_paths),
        "skipped": len(skipped),
        "crop": args.crop,
        "dimensions": list(dimensions),
        "timestamp": args.timestamp,
        "results": [asdict(result) for result in results],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return generated, results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 DailyPhoto 照片按人脸特征点对齐并生成视频或 GIF")
    parser.add_argument("--input", type=Path, default=configured_photos_root(), help="原始照片目录")
    parser.add_argument("--output", type=Path, default=ROOT / "generated", help="派生文件目录")
    parser.add_argument(
        "--format",
        choices=("mp4", "gif", "both"),
        default="mp4",
        help="输出格式；默认 mp4",
    )
    parser.add_argument("--gif-name", default="daily-photo.gif", help="GIF 文件名")
    parser.add_argument("--video-name", default="daily-photo.mp4", help="MP4 文件名")
    parser.add_argument(
        "--crop",
        choices=("face", "wide"),
        default="wide",
        help="构图范围；face 为人脸特写，wide 为较大范围的 16:9 画面",
    )
    parser.add_argument("--size", type=int, default=800, help="输出宽度（像素）")
    parser.add_argument("--duration", type=int, default=250, help="每帧显示时长（毫秒）")
    parser.add_argument("--quality", type=int, default=92, help="对齐 JPEG 质量（1–100）")
    parser.add_argument("--confidence", type=float, default=0.75, help="人脸检测置信度（0–1）")
    parser.add_argument("--background-blur", type=int, default=0, help="背景模糊核大小；0 表示关闭")
    parser.add_argument(
        "--timestamp",
        choices=("none", "date", "datetime"),
        default="date",
        help="每帧时间标记；默认显示日期",
    )
    parser.add_argument("--strict", action="store_true", help="任一照片失败时不生成输出")
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="也处理不符合 DailyPhoto 标准文件名的图片",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=resource_path(Path("models") / DEFAULT_MODEL_NAME),
        help="YuNet ONNX 模型路径",
    )
    args = parser.parse_args()
    if args.size < 128:
        parser.error("--size 不能小于 128")
    if args.duration < 20:
        parser.error("--duration 不能小于 20")
    if not 1 <= args.quality <= 100:
        parser.error("--quality 必须在 1–100 之间")
    if not 0 < args.confidence <= 1:
        parser.error("--confidence 必须在 0–1 之间")
    return args


def main() -> int:
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        output_paths, results = create_timelapse(args)
    except (OSError, ValueError, cv2.error) as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1
    skipped = [result for result in results if result.status != "aligned"]
    print("\n已生成：")
    for path in output_paths:
        print(f"  {path}")
    print(f"成功 {len(results) - len(skipped)} 张，跳过 {len(skipped)} 张")
    for result in skipped:
        print(f"  - {result.source}: {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
