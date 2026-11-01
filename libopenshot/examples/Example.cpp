/**
 * @file
 * @brief Example application showing how to attach VideoCacheThread to an FFmpegReader
 * @author Jonathan Thomas <jonathan@openshot.org>
 *
 * @ref License
 */

// Copyright (c) 2008-2025 OpenShot Studios, LLC
//
// SPDX-License-Identifier: LGPL-3.0-or-later

#include <chrono>
#include <iostream>
#include <memory>
#include <string>
#include "Clip.h"
#include "Frame.h"
#include "FFmpegReader.h"
#include "Settings.h"
#include "Timeline.h"

using namespace openshot;

int main(int argc, char* argv[]) {
    using clock = std::chrono::high_resolution_clock;
    auto total_start = clock::now();

    const std::string output_dir = "/home/jonathan/Downloads";
    const std::string input_paths[] = {
        "/home/jonathan/Videos/3.4 Release/Screencasts/Timing.mp4",
        "/home/jonathan/Downloads/openshot-testing/sintel_trailer-720p.mp4"
    };
    const int64_t frames_to_fetch[] = {175, 225, 240, 500, 1000};
    const bool use_hw_decode = false;

    std::cout << "Hardware decode: " << (use_hw_decode ? "ON" : "OFF") << "\n";
    openshot::Settings::Instance()->HARDWARE_DECODER = use_hw_decode ? 1 : 0;

    for (const std::string& input_path : input_paths) {
        auto file_start = clock::now();
        std::string base = input_path;
        size_t slash = base.find_last_of('/');
        if (slash != std::string::npos) {
            base = base.substr(slash + 1);
        }

        std::cout << "\n=== File: " << base << " ===\n";

        auto t0 = clock::now();
        FFmpegReader reader(input_path.c_str());
        auto t1 = clock::now();
        std::cout << "FFmpegReader ctor: "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count()
                  << " ms\n";

        auto t2 = clock::now();
        reader.Open();
        auto t3 = clock::now();
        std::cout << "FFmpegReader Open(): "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(t3 - t2).count()
                  << " ms\n";

        auto t4 = clock::now();
        Timeline timeline(1920, 1080, Fraction(30, 1), reader.info.sample_rate, reader.info.channels, reader.info.channel_layout);
        timeline.SetMaxSize(640, 480);
        auto t5 = clock::now();
        std::cout << "Timeline ctor (1080p30): "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(t5 - t4).count()
                  << " ms\n";

        auto t6 = clock::now();
        Clip c1(&reader);
        auto t7 = clock::now();
        std::cout << "Clip ctor: "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(t7 - t6).count()
                  << " ms\n";

        timeline.AddClip(&c1);

        auto t8 = clock::now();
        timeline.Open();
        auto t9 = clock::now();
        std::cout << "Timeline Open(): "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(t9 - t8).count()
                  << " ms\n";

        for (int64_t frame_number : frames_to_fetch) {
            auto loop_start = clock::now();
            std::cout << "Requesting frame " << frame_number << "...\n";

            auto t10 = clock::now();
            std::shared_ptr<Frame> frame = timeline.GetFrame(frame_number);
            auto t11 = clock::now();
            std::cout << "Timeline GetFrame(" << frame_number << "): "
                      << std::chrono::duration_cast<std::chrono::milliseconds>(t11 - t10).count()
                      << " ms\n";

            std::string out_path = output_dir + "/frame-" + base + "-" + std::to_string(frame_number) + ".jpg";

            auto t12 = clock::now();
            frame->Thumbnail(out_path, 200, 80, "", "", "#000000", false, "JPEG", 95, 0.0f);
            auto t13 = clock::now();
            std::cout << "Frame Thumbnail() JPEG (" << frame_number << "): "
                      << std::chrono::duration_cast<std::chrono::milliseconds>(t13 - t12).count()
                      << " ms\n";

            auto loop_end = clock::now();
            std::cout << "Frame loop total (" << frame_number << "): "
                      << std::chrono::duration_cast<std::chrono::milliseconds>(loop_end - loop_start).count()
                      << " ms\n";
        }

        reader.Close();
        timeline.Close();

        auto file_end = clock::now();
        std::cout << "File total (" << base << "): "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(file_end - file_start).count()
                  << " ms\n";
    }

    auto total_end = clock::now();
    std::cout << "Total elapsed: "
              << std::chrono::duration_cast<std::chrono::milliseconds>(total_end - total_start).count()
              << " ms\n";
    return 0;
}
