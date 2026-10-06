"""
The All Scenes page: small cached cover thumbnails, sent for the scenes asked
for, and scene list entries kept until their files change.
"""

import asyncio
import base64
import io
import json
import os

import pytest
from PIL import Image

import talemate.scene_thumbnails as scene_thumbnails
import talemate.server.websocket_server as websocket_server
from talemate.server.websocket_server import WebsocketHandler


def save_image(path, size, mode="RGB", fmt="PNG"):
    # see-through where there is alpha (WebP drops an opaque alpha channel)
    color = {"P": 0, "RGBA": (255, 0, 0, 128)}.get(mode, "red")
    image = Image.new(mode, size, color)
    if mode == "P":
        image.info["transparency"] = 0
        image.save(path, fmt, transparency=0)
    else:
        image.save(path, fmt)
    return path


def open_thumbnail(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize(
    "size, expected",
    [
        # portrait: scaled to cover 320x440, by its width
        ((1664, 2432), (320, 468)),
        # landscape: by its height
        ((1920, 1080), (782, 440)),
        # small images aren't made larger
        ((200, 150), (200, 150)),
        # very wide: long side capped
        ((8000, 1000), (880, 110)),
    ],
)
def test_thumbnail_size(tmp_path, size, expected):
    path = save_image(tmp_path / "cover.png", size)
    image = open_thumbnail(scene_thumbnails.make_thumbnail(str(path)))
    assert image.format == "WEBP"
    assert image.size == expected


@pytest.mark.parametrize(
    "mode, fmt, alpha",
    [
        ("RGBA", "PNG", True),
        ("P", "PNG", True),
        ("L", "PNG", False),
        ("RGB", "JPEG", False),
        ("CMYK", "JPEG", False),
    ],
)
def test_thumbnail_modes(tmp_path, mode, fmt, alpha):
    path = save_image(tmp_path / f"cover.{fmt.lower()}", (900, 1200), mode, fmt)
    image = open_thumbnail(scene_thumbnails.make_thumbnail(str(path)))
    assert image.size == (330, 440)
    assert ("A" in image.mode) is alpha


def test_thumbnail_is_cached_until_the_image_changes(tmp_path, monkeypatch):
    path = save_image(tmp_path / "cover.png", (1000, 1000))
    cache_dir = tmp_path / "cache"

    made = []
    make = scene_thumbnails.make_thumbnail

    def counting(image_path):
        made.append(image_path)
        return make(image_path)

    monkeypatch.setattr(scene_thumbnails, "make_thumbnail", counting)

    first = scene_thumbnails.thumbnail(str(path), str(cache_dir))
    second = scene_thumbnails.thumbnail(str(path), str(cache_dir))
    assert first == second
    assert len(made) == 1
    assert [f.suffix for f in cache_dir.iterdir()] == [".webp"]

    # a new image under the same name
    save_image(path, (500, 800))
    later = os.path.getmtime(path) + 10
    os.utime(path, (later, later))
    third = scene_thumbnails.thumbnail(str(path), str(cache_dir))
    assert len(made) == 2
    assert open_thumbnail(third).size == (320, 512)


def test_thumbnail_without_a_writable_cache(tmp_path):
    path = save_image(tmp_path / "cover.png", (1000, 1000))
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory")
    data = scene_thumbnails.thumbnail(str(path), str(blocked / "cache"))
    assert open_thumbnail(data).size == (440, 440)


# ---------------------------------------------------------------------------
# websocket handler
# ---------------------------------------------------------------------------


@pytest.fixture
def scenes(tmp_path, monkeypatch):
    scenes_dir = tmp_path / "scenes"
    (scenes_dir / "project" / "assets").mkdir(parents=True)
    monkeypatch.setattr(
        websocket_server.Scene, "scenes_dir", classmethod(lambda cls: str(scenes_dir))
    )
    return scenes_dir


@pytest.fixture
def handler(monkeypatch):
    handler = WebsocketHandler.__new__(WebsocketHandler)
    handler.sent = []
    handler.queue_put = handler.sent.append
    monkeypatch.setattr(websocket_server, "SCENE_LIST_CACHE", {})
    return handler


@pytest.mark.asyncio
async def test_request_scene_thumbnails(scenes, handler):
    scene_path = scenes / "project" / "scene.json"
    save_image(scenes / "project" / "assets" / "abc.png", (1200, 1800))

    handler.request_scene_thumbnails(
        [
            {"path": str(scene_path), "id": "abc", "file_type": "png"},
            # missing: skipped, the others still sent
            {"path": str(scene_path), "id": "missing", "file_type": "png"},
            # outside the scenes directory: refused
            {
                "path": str(scenes.parent / "x" / "scene.json"),
                "id": "abc",
                "file_type": "png",
            },
            {"path": str(scene_path), "id": "../../../secret", "file_type": "png"},
        ]
    )
    await handler._thumbnails_task

    assert [(m["type"], m["id"], m["media_type"]) for m in handler.sent] == [
        ("scene_thumbnail", "abc", "image/webp")
    ]
    image = open_thumbnail(base64.b64decode(handler.sent[0]["base64"]))
    assert image.size == (320, 480)
    # cached next to the scenes directory
    assert len(list((scenes.parent / "cache" / "scene_thumbnails").iterdir())) == 1


@pytest.mark.asyncio
async def test_a_new_thumbnail_request_replaces_the_previous(scenes, handler):
    scene_path = scenes / "project" / "scene.json"
    for name in "abcd":
        save_image(scenes / "project" / "assets" / f"{name}.png", (600, 800))

    handler.request_scene_thumbnails(
        [{"path": str(scene_path), "id": n, "file_type": "png"} for n in "abc"]
    )
    first = handler._thumbnails_task
    handler.request_scene_thumbnails(
        [{"path": str(scene_path), "id": "d", "file_type": "png"}]
    )
    await asyncio.gather(first, return_exceptions=True)
    await handler._thumbnails_task

    assert first.cancelled()
    assert [m["id"] for m in handler.sent] == ["d"]


def write_scene(path, **data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_scene_list_items_are_kept_until_the_scene_changes(
    scenes, handler, monkeypatch
):
    scene_path = scenes / "project" / "scene.json"
    write_scene(scene_path, name="One", description="First.")

    reads = []
    read = WebsocketHandler._read_scene_list_item

    def counting(self, path):
        reads.append(path)
        return read(self, path)

    monkeypatch.setattr(WebsocketHandler, "_read_scene_list_item", counting)

    item = handler._scene_list_item(str(scene_path))
    assert (item["name"], item["description"], item["cover_image"]) == (
        "One",
        "First.",
        None,
    )
    # a copy: changing it doesn't change the cache
    item["name"] = "changed"
    assert handler._scene_list_item(str(scene_path))["name"] == "One"
    assert len(reads) == 1

    write_scene(scene_path, name="Two, a longer name", description="Second.")
    assert handler._scene_list_item(str(scene_path))["name"] == "Two, a longer name"
    assert len(reads) == 2

    # the cover image comes from the asset library
    save_image(scenes / "project" / "assets" / "cover.png", (100, 100))
    library = scenes / "project" / "assets" / "library.json"
    library.write_text(
        json.dumps(
            {
                "assets": {
                    "cover": {
                        "id": "cover",
                        "file_type": "png",
                        "media_type": "image/png",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    write_scene(
        scene_path,
        name="Two, a longer name",
        description="Second.",
        assets={"cover_image": "cover"},
    )
    assert handler._scene_list_item(str(scene_path))["cover_image"]["id"] == "cover"


@pytest.mark.asyncio
async def test_scene_list_with_metadata_is_sent(scenes, handler, monkeypatch):
    scene_path = scenes / "project" / "scene.json"
    write_scene(scene_path, name="One")
    monkeypatch.setattr(
        websocket_server, "list_scenes_directory", lambda list_images: [str(scene_path)]
    )

    handler.request_scenes_list("", False, True)
    await asyncio.gather(*handler._background_tasks)

    assert handler.sent[-1]["type"] == "scenes_list"
    assert handler.sent[-1]["metadata"] is True
    assert [scene["name"] for scene in handler.sent[-1]["data"]] == ["One"]

    # without metadata, at once
    handler.request_scenes_list("", False, False)
    assert handler.sent[-1]["metadata"] is False
    assert handler.sent[-1]["data"][0]["path"] == str(scene_path)
