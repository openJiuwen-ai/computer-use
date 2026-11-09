// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;

internal static class WindowsCaptureTimeoutHelper
{
    static int Main()
    {
        string frame = Console.ReadLine();
        if (frame == null)
            return 1;
        var serializer = new JavaScriptSerializer();
        var request = serializer.Deserialize<Dictionary<string, object>>(frame);
        string route = request["route"] as string;
        if (route != "wgc" && route != "print")
            return 2;
        string directory = AppDomain.CurrentDomain.BaseDirectory;
        File.WriteAllText(Path.Combine(directory, "capture-timeout-" + route + ".json"),
            serializer.Serialize(new Dictionary<string, object> { { "pid", Process.GetCurrentProcess().Id },
                                                                    { "handle", request["handle"] } }));
        Thread.Sleep(Timeout.Infinite);
        return 0;
    }
}
