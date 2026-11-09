// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

namespace ComputerUse.Windows
{
    // This alternate entry point is compiled only into the manual probe driver.
    // Its supervisor and native read/input worker run the production entry point.
    internal static class WindowsDirectoryTimeoutDriver
    {
        [MTAThread]
        static int Main(string[] args)
        {
            if (args.Length != 1 || args[0] != "--window-directory-worker")
                return (int)typeof(Program).GetMethod("Main", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(null, new object[] { args });

            string frame = Console.ReadLine();
            if (frame == null)
                return 1;
            long id = NativeWindowDiscovery.WorkerRequest(frame);
            using (var process = Process.GetCurrentProcess())
            {
                string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "directory-request.json");
                File.WriteAllText(path, Wire.Json(Wire.Obj("pid", process.Id, "request_id", id)));
            }
            Thread.Sleep(Timeout.Infinite);
            return 0;
        }
    }
}
