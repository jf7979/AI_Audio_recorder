#!/usr/bin/env python3
"""Lists audio input devices so you can pick one for config.yaml's audio.device."""
import sounddevice as sd


def main() -> None:
    print(f"Default input device: {sd.default.device[0]}\n")
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0:
            print(f"[{index}] {info['name']}  (channels={info['max_input_channels']}, "
                  f"default_sample_rate={info['default_samplerate']:.0f})")


if __name__ == "__main__":
    main()
