Riff 0.6.29 speeds up PNG-based MP4 exports by overlapping frame drawing, snapshots, encoding and upload.

Exports keep the selected picture size, frame rate, passage and encoding settings. The browser takes ordered snapshots, keeps two snapshots in flight, and updates progress and previews at an interactive cadence. Cancellation releases pending snapshots and the export worker.

Controlled self-hosted samples improved export wall time by about 1.3 to 1.6 times for 1080p60 two-second passages and 4K60 one-second passages. Their MP4 files were byte-identical to the baseline, including the video frames and audio. Matched H.264 samples also retained identical output. These measurements cover the tested browser and host; export time depends on the device, browser, picture size and recording length.

The native music engine and model requirements are unchanged.
