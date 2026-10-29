// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Linq;
using System.Text;

namespace ComputerUse.Windows
{
    // Platform composition is separate from the protocol/session dispatcher.
    internal static class Program
    {
        static NativeReadStore workerStore;
        static NativeReadBackend workerReader;
        static object[] checkpoint = new object[0];
        static NativeRuntime CreateRuntime(string session, string mode)
        {
            var reader = new NativeReadBackend(session, mode: mode);
            workerReader = reader;
            reader.Store.RestoreWindows(checkpoint);
            workerStore = reader.Store;
            bool foregroundAllowed = Environment.GetEnvironmentVariable("COMPUTER_USE_FOREGROUND_ASSIST") == "1";
            var captures = new NativePointerCaptures();
            var pointer = new NativePointer(new NativeWindowsPointer(reader.Store, captures, mode), mode, foregroundAllowed);
            var keyboard = new NativeKeyboard(new NativeUiaKeyboard(reader.Store, pointer, mode), mode, foregroundAllowed, new NativeWindowsClipboard());
            var desktopKeyboard = mode == "exclusive" ? new NativeKeyboard(new NativeDesktopKeyboard(captures), mode, false) : null;
            return new NativeRuntime(session, reader, new NativeExecution(new NativeUiaObjects(reader.Store), keyboard, pointer, new NativeScroll(new NativeUiaScroll(reader.Store)), desktopKeyboard, new NativeUiaCurrentReceiver(reader.Store)), new NativeCapture(reader.Store, session, mode, captures), AgentPointerFeedback.Point);
        }

        [MTAThread]
        static int Main(string[] args)
        {
            Console.InputEncoding = new UTF8Encoding(false);
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length == 1 && args[0] == "--window-directory-worker")
                return NativeWindowDiscovery.RunWorker();
            Native.Initialize();
            if (args.Contains("--check-permissions") || args.Contains("--request-permissions"))
            {
                bool available = Native.Interactive();
                Console.WriteLine(Wire.Json(Wire.Obj("accessibility", available, "screenCapture", available, "input", available, "platform", "windows", "detail", "interactive desktop availability only; individual providers and elevated targets are checked at operation time")));
                return 0;
            }

            try
            {
                if (args.Contains("--native-worker"))
                {
                    string line = Console.ReadLine();
                    if (line == null)
                        return 0;
                    var bootstrap = Wire.Object(Wire.Serializer().DeserializeObject(line));
                    checkpoint = Wire.Array(bootstrap["windows"]);
                    Console.WriteLine(Wire.Json(Wire.Obj("ready", true)));
                    Console.Out.Flush();
                    var driver = new Driver(CreateRuntime, Wire.Str(bootstrap, "session"), () => workerStore == null ? new object[0] : workerStore.WindowCheckpoint(), () => workerReader != null && workerReader.NeedsRestart, () => AgentPointerFeedback.Latest);
                    AgentPointerFeedback.Output = driver.Display;
                    try
                    {
                        driver.Run();
                    }
                    finally
                    {
                        AgentPointerFeedback.Output = null;
                    }
                }
                else
                {
                    NativeIsolatedRuntime runtime = null;
                    new Driver((session, mode) => runtime = new NativeIsolatedRuntime(session, mode), stopDisplay: () =>
                    {
                        if (runtime != null)
                            runtime.StopDisplay();
                    }).Run();
                }

                return 0;
            }
            catch (Exception e)
            {
                Console.Error.WriteLine(e);
                return 1;
            }
        }
    }
}
