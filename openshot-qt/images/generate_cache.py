"""
 @file
 @brief This file generates the image cache for openshot-qt, essentially creating multiple
 resolution versions of each image used in the UI, for faster loading and high DPI support.
 @author Jonathan Thomas <jonathan@openshot.org>

 @section LICENSE

 Copyright (c) 2008-2018 OpenShot Studios, LLC
 (http://www.openshotstudios.com). This file is part of
 OpenShot Video Editor (http://www.openshot.org), an open-source project
 dedicated to delivering high quality video editing and animation solutions
 to the world.

 OpenShot Video Editor is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 OpenShot Video Editor is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with OpenShot Library.  If not, see <http://www.gnu.org/licenses/>.
 """

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
SRC_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from classes import info
from classes.logger import log
import openshot
from qt_api import QApplication

# Try to get the security-patched XML functions from defusedxml
try:
    from defusedxml import minidom as xml
except ImportError:
    from xml.dom import minidom as xml


_svg_app = None


def ensure_qapplication():
    """Create a Qt application in the current process for SVG font rendering."""
    global _svg_app
    if QApplication.instance() is None:
        _svg_app = QApplication([])
    else:
        _svg_app = QApplication.instance()


def scale_path(path, scale):
    base, ext = os.path.splitext(path)
    return "%s@%dx%s" % (base, scale, ext)


def create_cache_thumbnail(path, width, height, thumb_path, skip_existing=False):
    """Create a thumbnail image of a specific image file at a specific size"""
    ensure_qapplication()
    display_scales = [1, 2]
    mask_path = os.path.join(info.IMAGES_PATH, "mask.png")
    errors = []

    for scale in display_scales:
        try:
            scale_thumb_path = thumb_path
            scale_width = width
            scale_height = height
            if scale > 1:
                # Create @2x version of cache image
                scale_width *= scale
                scale_height *= scale
                suffix = "@%dx" % scale
                thumb_path_base, thumb_path_ext = os.path.splitext(thumb_path)
                scale_thumb_path = "%s%s%s" % (thumb_path_base, suffix, thumb_path_ext)

            if skip_existing and os.path.exists(scale_thumb_path):
                continue

            source_path = path
            tmp_dir = None
            scale_source_path = scale_path(path, scale)
            if scale > 1 and os.path.exists(scale_source_path):
                # Qt treats @2x filenames as device-pixel-ratio images. Copy the
                # source to a neutral filename so libopenshot uses its full pixels.
                tmp_dir = tempfile.TemporaryDirectory()
                source_ext = os.path.splitext(path)[1]
                source_path = os.path.join(tmp_dir.name, "source_%dx%s" % (scale, source_ext))
                shutil.copyfile(scale_source_path, source_path)

            # Reload this reader
            clip = openshot.Clip(source_path)
            if scale > 1:
                clip.scale_x.AddPoint(1.0, 1.0 * scale)
                clip.scale_y.AddPoint(1.0, 1.0 * scale)
            reader = clip.Reader()

            # Open reader
            reader.Open()

            # Save thumbnail
            reader.GetFrame(0).Thumbnail(scale_thumb_path, scale_width, scale_height,
                                         mask_path, "", "#000", True, "png", 85)
            reader.Close()
            clip.Close()
            if tmp_dir:
                tmp_dir.cleanup()

        except Exception as exc:
            if 'tmp_dir' in locals() and tmp_dir:
                tmp_dir.cleanup()
            # Handle exception
            log.debug('Invalid cache image file %s', path, exc_info=1)
            errors.append("scale {}: {}".format(scale, exc))

    return errors


def _task(category, icon_path, size, thumb_path):
    return {
        "category": category,
        "icon_path": icon_path,
        "width": size.width(),
        "height": size.height(),
        "thumb_path": thumb_path,
    }


def collect_titles(icon_size):
    """Build thumbnail tasks for title templates."""
    tasks = []
    titles_dir = os.path.join(info.PATH, "titles")
    titles_list = [
        os.path.join(titles_dir, filename)
        for filename in sorted(os.listdir(titles_dir))
    ]

    for icon_path in sorted(titles_list):
        filename = os.path.basename(icon_path)
        fileBaseName = os.path.splitext(filename)[0]
        thumb_path = os.path.join(info.IMAGES_PATH, "cache", "{}.png".format(fileBaseName))
        tasks.append(_task("titles", icon_path, icon_size, thumb_path))

    return tasks


def collect_emojis(emoji_size):
    """Build thumbnail tasks for emoji SVGs."""
    tasks = []
    emojis_dir = os.path.join(info.PATH, "emojis", "color", "svg")

    for filename in sorted(os.listdir(emojis_dir)):
        icon_path = os.path.join(emojis_dir, filename)
        fileBaseName = os.path.splitext(filename)[0]
        thumb_path = os.path.join(info.IMAGES_PATH, "cache", "{}.png".format(fileBaseName))
        tasks.append(_task("emojis", icon_path, emoji_size, thumb_path))

    return tasks


def collect_effects(icon_size):
    """Build thumbnail tasks for effect icons."""
    tasks = []
    effects_dir = os.path.join(info.PATH, "effects")
    icons_dir = os.path.join(effects_dir, "icons")

    raw_effects_list = json.loads(openshot.EffectInfo.Json())
    for effect_info in raw_effects_list:
        effect_name = effect_info["class_name"]
        icon_name = "%s.png" % effect_name.lower().replace(' ', '')
        icon_path = os.path.join(icons_dir, icon_name)
        thumb_path = os.path.join(info.IMAGES_PATH, "cache", icon_name)
        tasks.append(_task("effects", icon_path, icon_size, thumb_path))

    return tasks


def collect_blender(icon_size):
    """Build thumbnail tasks for Blender title icons."""
    tasks = []
    blender_dir = os.path.join(info.PATH, "blender")
    icons_dir = os.path.join(blender_dir, "icons")

    for filename in sorted(os.listdir(blender_dir)):
        path = os.path.join(blender_dir, filename)
        if os.path.isfile(path) and ".xml" in filename:
            xmldoc = xml.parse(path)
            icon_name = xmldoc.getElementsByTagName("icon")[0].childNodes[0].data
            icon_path = os.path.join(icons_dir, icon_name)
            thumb_path = os.path.join(info.IMAGES_PATH, "cache", "blender_{}".format(icon_name))
            tasks.append(_task("blender", icon_path, icon_size, thumb_path))

    return tasks


def collect_transitions(icon_size):
    """Build thumbnail tasks for transition icons."""
    tasks = []
    transitions_dir = os.path.join(info.PATH, "transitions")
    common_dir = os.path.join(transitions_dir, "common")
    extra_dir = os.path.join(transitions_dir, "extra")
    transition_groups = [
        ("common", common_dir, os.listdir(common_dir)),
        ("extra", extra_dir, os.listdir(extra_dir)),
    ]

    for _, dir_name, files in transition_groups:
        for filename in sorted(files):
            if filename[0] == "." or "thumbs.db" in filename.lower():
                continue

            icon_path = os.path.join(dir_name, filename)
            fileBaseName = os.path.splitext(filename)[0]
            thumb_path = os.path.join(info.IMAGES_PATH, "cache", "{}.png".format(fileBaseName))
            tasks.append(_task("transitions", icon_path, icon_size, thumb_path))

    return tasks


def collect_tasks(categories):
    icon_size = info.LIST_ICON_SIZE
    emoji_size = info.EMOJI_ICON_SIZE
    collectors = {
        "titles": lambda: collect_titles(icon_size),
        "emojis": lambda: collect_emojis(emoji_size),
        "effects": lambda: collect_effects(icon_size),
        "blender": lambda: collect_blender(icon_size),
        "transitions": lambda: collect_transitions(icon_size),
    }

    tasks = []
    for category in categories:
        tasks.extend(collectors[category]())
    return tasks


def run_task(task, skip_existing=False):
    errors = create_cache_thumbnail(
        task["icon_path"],
        task["width"],
        task["height"],
        task["thumb_path"],
        skip_existing=skip_existing,
    )
    return task, errors


def parse_args():
    cpu_count = multiprocessing.cpu_count()
    default_jobs = min(8, max(1, cpu_count - 1))
    parser = argparse.ArgumentParser(
        description="Generate OpenShot UI thumbnail cache images."
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=default_jobs,
        help="number of worker processes (default: %(default)s)",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=["titles", "emojis", "effects", "blender", "transitions"],
        help="category to generate; can be specified more than once",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip cache files that already exist",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    categories = args.category or ["titles", "emojis", "effects", "blender", "transitions"]
    tasks = collect_tasks(categories)
    total = len(tasks)
    jobs = max(1, args.jobs)
    started = time.time()

    print("Generating {} cache thumbnails with {} worker{}...".format(
        total,
        jobs,
        "" if jobs == 1 else "s",
    ))

    failures = []
    completed = 0

    if jobs == 1:
        ensure_qapplication()
        for task in tasks:
            task, errors = run_task(task, skip_existing=args.skip_existing)
            completed += 1
            if errors:
                failures.append((task, errors))
            if completed % 100 == 0 or completed == total:
                print("Generated {}/{}".format(completed, total))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            future_map = {
                executor.submit(run_task, task, args.skip_existing): task
                for task in tasks
            }
            for future in as_completed(future_map):
                completed += 1
                try:
                    task, errors = future.result()
                except Exception as exc:
                    task = future_map[future]
                    errors = [str(exc)]
                if errors:
                    failures.append((task, errors))
                if completed % 100 == 0 or completed == total:
                    print("Generated {}/{}".format(completed, total))

    elapsed = time.time() - started
    print("Finished in {:.1f}s".format(elapsed))

    if failures:
        print("Failed to generate {} thumbnail{}:".format(
            len(failures),
            "" if len(failures) == 1 else "s",
        ))
        for task, errors in failures[:25]:
            print("  {}: {}".format(task["icon_path"], "; ".join(errors)))
        if len(failures) > 25:
            print("  ... and {} more".format(len(failures) - 25))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
