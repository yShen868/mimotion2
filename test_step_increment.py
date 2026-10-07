# -*- coding: utf8 -*-
"""手动测试步数写入。成功后自增 1：第一次 5100，第二次 5101，第三次 5102。"""
import json
import os
import sys

import main as mimotion

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_step_state.json")
START_STEP = 5100


def read_next_step(path=STATE_FILE):
    if not os.path.exists(path):
        return START_STEP
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data["next_step"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        print(f"测试步数记录无法读取，从 {START_STEP} 重新开始")
        return START_STEP


def write_next_step(step, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"next_step": int(step)}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    step = read_next_step()
    print(f"本次测试步数：{step}")
    print(f"若本次写入成功，下次为：{step + 1}")
    success = mimotion.run_local(fixed_step=step)
    if not success:
        print(f"写入失败，下次仍使用 {step}")
        sys.exit(1)
    write_next_step(step + 1)
    print(f"写入成功，已记录下次测试步数：{step + 1}")


if __name__ == "__main__":
    main()
