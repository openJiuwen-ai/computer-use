# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""osworld/verified_patches.py: Verified's setup.py must survive a Windows host.

chrome 44ee5668's setup leaves its history file open inside a TemporaryDirectory;
on Windows removing that directory raised WinError 32 and the task could never
start. The patch must stop that error without changing anything else in tempfile.
"""
from __future__ import annotations

import os
import tempfile
import types

import pytest

from osworld.verified_patches import slow_scripted_waits, tolerate_setup_tempdir_cleanup


def _setup_module():
    module = types.ModuleType("fake_setup")
    module.tempfile = tempfile
    return module


def test_open_file_no_longer_breaks_tempdir_exit():
    module = _setup_module()
    tolerate_setup_tempdir_cleanup(module)
    with module.tempfile.TemporaryDirectory() as tmp_dir:
        held = open(os.path.join(tmp_dir, "history_empty.sqlite"), "wb")  # what setup.py leaves open
    held.close()


def test_rest_of_tempfile_is_untouched():
    module = _setup_module()
    tolerate_setup_tempdir_cleanup(module)
    assert module.tempfile.NamedTemporaryFile is tempfile.NamedTemporaryFile
    assert module.tempfile.gettempdir() == tempfile.gettempdir()


def test_global_tempfile_is_not_patched():
    original = tempfile.TemporaryDirectory
    tolerate_setup_tempdir_cleanup(_setup_module())
    assert tempfile.TemporaryDirectory is original


class _Controller:
    def __init__(self):
        self.calls = []

    def setup(self, config):  # Verified dispatches each step to _<type>_setup(**parameters)
        for step in config:
            getattr(self, f"_{step['type']}_setup")(**step["parameters"])

    def _sleep_setup(self, seconds):
        self.calls.append(("sleep", seconds))

    def _execute_setup(self, command, stdout="", stderr="", shell=False, until=None):
        self.calls.append(("execute", command))


POSTCONFIG = [{"type": "execute", "parameters": {"command": [
                  "python3", "-c",
                  "import time, pyautogui; time.sleep(1); pyautogui.hotkey('shift', 'ctrl', 'e'); time.sleep(0.5)"]}},
              {"type": "sleep", "parameters": {"seconds": 5}}]


def _env_classes():
    controller_cls = type("Controller", (_Controller,), {})

    class Env:
        def __init__(self):
            self.setup_controller = controller_cls()

        def evaluate(self):
            self.setup_controller.setup(POSTCONFIG)
            return 1.0

        def reset(self, task_config):
            self.setup_controller = controller_cls()  # as _start_emulator does on a used environment
            self.setup_controller.setup(task_config["config"])

    slow_scripted_waits(Env, controller_cls, evaluate_factor=3.0, reset_factor=2.0)
    return Env


def test_grader_postconfig_waits_three_times_longer():
    env = _env_classes()()
    assert env.evaluate() == pytest.approx(1.0)
    (_, command), sleep = env.setup_controller.calls
    assert "time.sleep(3)" in command[2] and "time.sleep(1.5)" in command[2] and "hotkey" in command[2]
    assert sleep == ("sleep", 15.0)


def test_task_setup_outside_evaluate_keeps_its_waits():
    env = _env_classes()()
    env.setup_controller.setup(POSTCONFIG)
    (_, command), sleep = env.setup_controller.calls
    assert "time.sleep(1)" in command[2] and sleep == ("sleep", 5)


def test_task_setup_in_reset_waits_longer_even_on_a_new_controller():
    env = _env_classes()()
    env.reset({"config": POSTCONFIG})
    (_, command), sleep = env.setup_controller.calls
    assert "time.sleep(2)" in command[2] and "time.sleep(1)" in command[2] and sleep == ("sleep", 10.0)
    env.setup_controller.setup(POSTCONFIG)
    assert env.setup_controller.calls[-1] == ("sleep", 5)
