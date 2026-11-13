# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Targeted evidence from captures crossing the view and operation boundary."""

from collections.abc import Mapping



def captured_metadata(snapshot):
    return snapshot


def captured_identity(snapshot):
    return snapshot.capture


def captured_node(snapshot, token, *fields):
    if token is None:
        return None
    return snapshot.capture.node_view(token, fields)


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)
