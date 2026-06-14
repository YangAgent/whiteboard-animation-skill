#!/usr/bin/env python3
"""
Whiteboard Video Workflow Helper

Provides three commands:
  1. init-dirs     - Create storyboard/image/video output directories
  2. gen-prompts   - Parse storyboard.json and generate image prompts with whiteboard style prefix
  3. merge-videos  - Merge video segments into one final video using PyAV

Usage:
    python workflow_helper.py init-dirs <output-dir>
    python workflow_helper.py gen-prompts <storyboard-json-path>
    python workflow_helper.py merge-videos <output-dir> <video1> <video2> ...
"""

import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path


def ends_with_symbol(text: str) -> bool:
    """Return True when the stripped text already ends with punctuation or a symbol."""
    stripped = text.rstrip()
    if not stripped:
        return False
    return unicodedata.category(stripped[-1])[0] in {"P", "S"}


def ensure_ending(text: str, ending: str) -> str:
    """Append ending only when the stripped text does not already end with a symbol."""
    stripped = text.strip()
    if not stripped:
        return ""
    if ends_with_symbol(stripped):
        return stripped
    return f"{stripped}{ending}"


def join_scene_text(text_parts: list[str]) -> str:
    """Join scene segment texts while preserving existing ending symbols."""
    parts = [text.strip() for text in text_parts if text.strip()]
    if not parts:
        return ""

    pieces = [ensure_ending(text, "，") for text in parts[:-1]]
    pieces.append(ensure_ending(parts[-1], "。"))
    return "".join(pieces)


def init_dirs(output_dir: str):
    """Create storyboard, image, video subdirectories under output_dir."""
    base = Path(output_dir).resolve()
    for name in ("storyboard", "image", "video"):
        (base / name).mkdir(parents=True, exist_ok=True)
    print(json.dumps({
        "status": "ok",
        "storyboardDir": str(base / "storyboard"),
        "imageDir": str(base / "image"),
        "videoDir": str(base / "video"),
    }))


def gen_prompts(storyboard_path: str):
    """Parse storyboard.json and output a JSON array of image prompts."""
    sb = json.loads(Path(storyboard_path).read_text(encoding="utf-8"))
    prompts = []
    for scene in sb.get("scenes", []):
        visual_hint = ensure_ending(scene.get("visualHint", ""), "。")
        if visual_hint:
            content = f'视觉元素建议：\n"{visual_hint}"'
        else:
            content = ""
        prompts.append(content)
    print(json.dumps(prompts, ensure_ascii=False))


def merge_videos(output_dir: str, video_paths: list[str]):
    """Merge multiple video segments into one final video using PyAV (re-encode via H.264)."""
    if not video_paths:
        print(json.dumps({"status": "error", "error": "没有视频片段可合并"}))
        sys.exit(1)

    # 检查所有视频文件是否存在
    for vp in video_paths:
        if not Path(vp).exists():
            print(json.dumps({"status": "error", "error": f"视频文件不存在: {vp}"}))
            sys.exit(1)

    # 生成输出文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir).resolve() / f"whiteboard_{timestamp}.mp4"

    import av
    from fractions import Fraction

    try:
        # 从第一个片段获取编码参数
        first_input = av.open(video_paths[0], mode="r")
        in_stream = first_input.streams.video[0]
        width = in_stream.codec_context.width
        height = in_stream.codec_context.height
        fps = in_stream.average_rate
        first_input.close()

        # 创建输出容器
        time_base = Fraction(1, int(fps))

        output_container = av.open(str(output_path), mode="w")
        out_stream = output_container.add_stream("h264", rate=fps)
        out_stream.width = width
        out_stream.height = height
        out_stream.pix_fmt = "yuv420p"
        out_stream.time_base = time_base
        out_stream.options = {"crf": "18"}

        # 逐个读取输入片段，解码后重新编码写入输出
        # 使用帧计数器生成单调递增的 PTS，避免多段拼接时时间戳倒退
        frame_count = 0
        for vp in video_paths:
            input_container = av.open(vp, mode="r")
            for frame in input_container.decode(video=0):
                frame.pts = frame_count
                frame.time_base = time_base
                frame_count += 1
                packet = out_stream.encode(frame)
                if packet:
                    for p in packet:
                        output_container.mux(p)
            input_container.close()

        # flush
        packet = out_stream.encode(None)
        if packet:
            for p in packet:
                output_container.mux(p)
        output_container.close()
    except Exception as e:
        # 清理可能残留的输出文件
        if output_path.exists():
            output_path.unlink()
        print(json.dumps({"status": "error", "error": f"视频合并失败: {e}"}))
        sys.exit(1)

    output_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(json.dumps({
        "status": "ok",
        "mergedVideo": str(output_path),
        "totalSegments": len(video_paths),
        "sizeMB": round(output_size_mb, 1),
    }, ensure_ascii=False))


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  workflow_helper.py init-dirs <output-dir>")
        print("  workflow_helper.py gen-prompts <storyboard-json-path>")
        print("  workflow_helper.py merge-videos <output-dir> <video1> <video2> ...")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "init-dirs":
        init_dirs(sys.argv[2])
    elif cmd == "gen-prompts":
        gen_prompts(sys.argv[2])
    elif cmd == "merge-videos":
        if len(sys.argv) < 4:
            print("Error: merge-videos requires output-dir and at least one video path")
            sys.exit(1)
        merge_videos(sys.argv[2], sys.argv[3:])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
