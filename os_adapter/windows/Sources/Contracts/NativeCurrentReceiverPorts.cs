// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // QUERY evidence only. These facts do not authorize a native input unit.
    internal sealed class NativeCurrentReceiverEvidence
    {
        internal readonly string State, Node, Detail;
        internal readonly string[] Ancestors;
        internal NativeCurrentReceiverEvidence(string state, string node = null, string[] ancestors = null, string detail = null)
        {
            State = state;
            Node = node;
            Ancestors = ancestors ?? new string[0];
            Detail = detail;
        }

        internal object WireValue()
        {
            var value = Wire.Obj("state", State, "node", Node, "ancestors", Ancestors);
            if (Detail != null)
                value["detail"] = Detail;
            return value;
        }
    }

    internal interface INativeCurrentReceiverProvider
    {
        NativeCurrentReceiverEvidence Read(Dictionary<string, object> scope, Request request);
    }

    // The discovery algorithm cannot enumerate siblings or descendants. Native
    // objects and registrations remain in the same platform execution boundary.
    internal interface INativeCurrentReceiverTree
    {
        object Focused(Request request);
        object Parent(object element, Request request);
        string Identity(object element, Request request);
        string Retain(object element, Request request);
    }
}
