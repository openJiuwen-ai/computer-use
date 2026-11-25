# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tools the OSWorld entry point (osworld/vm_agent.py) adds to this repo's agent.

execution.py      OSWorldExecutionEnvironment: commands through the guest server's /execute (the env snapshot)
infeasible.py     declare_infeasible and its marker, which the runner turns into OSWorld's FAIL
osworld_tools.py  inspect_file and read_skill, the skills directory, and the skills section of the system prompt
guest/            the inspector scripts inspect_file runs in the VM
"""
