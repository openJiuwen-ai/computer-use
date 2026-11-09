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
        static NativePointerCaptures workerCaptures;
        static object[] checkpoint = new object[0];
        static object[] capturesCheckpoint = new object[0];
        static NativeRuntime CreateRuntime(string session, bool globalInputAllowed)
        {
            var reader = new NativeReadBackend(session);
            workerReader = reader;
            reader.Store.RestoreWindows(checkpoint);
            workerStore = reader.Store;
            var captures = new NativePointerCaptures();
            foreach (object raw in capturesCheckpoint)
                captures.Retain(NativePointerCapture.Restore(raw, session));
            workerCaptures = captures;
            var pointer = new NativePointer(new NativeWindowsPointer(reader.Store, captures, globalInputAllowed));
            var clipboard = new NativeWindowsClipboard();
            var keyboard = new NativeKeyboard(new NativeUiaKeyboard(reader.Store, pointer, allowGlobalInput: globalInputAllowed), clipboard);
            var desktopKeyboard = new NativeKeyboard(new NativeDesktopKeyboard(globalInputAllowed), clipboard);
            return new NativeRuntime(session, reader, new NativeExecution(new NativeUiaObjects(reader.Store), keyboard, pointer, new NativeScroll(new NativeUiaScroll(reader.Store)), desktopKeyboard, new NativeUiaCurrentReceiver(reader.Store)), new NativeCapture(reader.Store, session, captures), AgentPointerFeedback.Point);
        }

        [MTAThread]
        static int Main(string[] args)
        {
            Console.InputEncoding = new UTF8Encoding(false);
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length == 1 && args[0] == "--window-directory-worker")
                return NativeWindowDiscovery.RunWorker();
            if (args.Length == 1 && args[0] == "--application-name-worker")
                return NativeApplicationNames.RunWorker();
            Native.Initialize();
            if (args.Contains("--check-permissions") || args.Contains("--request-permissions"))
            {
                Console.WriteLine(Wire.Json(NativeInputAccess.Permissions()));
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
                    capturesCheckpoint = Wire.Array(bootstrap["captures"]);
                    Console.WriteLine(Wire.Json(Wire.Obj("ready", true)));
                    Console.Out.Flush();
                    var driver = new Driver(CreateRuntime, Wire.Str(bootstrap, "session"), () => workerStore == null ? new object[0] : workerStore.WindowCheckpoint(), () => workerReader != null && workerReader.NeedsRestart, () => AgentPointerFeedback.Latest,
                                            captureCheckpoint: id => workerCaptures.Checkpoint(id));
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
                    new Driver((session, globalInputAllowed) => runtime = new NativeIsolatedRuntime(session, globalInputAllowed), stopDisplay: () =>
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
