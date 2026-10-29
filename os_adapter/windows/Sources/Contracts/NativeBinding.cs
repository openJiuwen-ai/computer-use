// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal static class NativeBinding
    {
        internal static readonly string[] Keys =
        {
            "session",
            "instance",
            "rootWindow",
            "surface",
            "node",
            "capture"
        };
        internal static Dictionary<string, object> Resolve(object raw, string session)
        {
            var value = Wire.Object(raw);
            Wire.Keys(value, String.Join(",", Keys));
            foreach (var key in Keys)
                if (value[key] != null)
                    Wire.Str(value, key);
            if (!Equals(value["session"], session))
                throw new Failure("stale_target", "target belongs to another native session");
            bool window = value["surface"] != null;
            if ((value["instance"] != null) != window || (value["rootWindow"] != null) != window || !window && value["node"] != null)
                throw new Failure("invalid_request", "native binding has an incomplete window scope");
            if (value["capture"] != null && value["node"] != null)
                throw new Failure("invalid_request", "image input cannot select an accessibility node");
            return new Dictionary<string, object>(value);
        }
    }
}
