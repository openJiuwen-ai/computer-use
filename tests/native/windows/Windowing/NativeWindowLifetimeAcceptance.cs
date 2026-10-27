// Run in an interactive Windows session. Uses production directory assembly
// with two disposable, non-activating native windows; never injects input.
// Compile with production sources and /main:ComputerUse.Windows.NativeWindowLifetimeAcceptance.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    internal static class NativeWindowLifetimeAcceptance
    {
        sealed class QuietWindow : Form
        {
            protected override bool ShowWithoutActivation
            {
                get
                {
                    return true;
                }
            }

            internal QuietWindow(string title, int x)
            {
                Text = title;
                StartPosition = FormStartPosition.Manual;
                Location = new Point(x, 20);
                Size = new Size(210, 90);
                ShowInTaskbar = false;
            }
        }

        static void Assert(bool value, string message)
        {
            if (!value)
                throw new InvalidOperationException(message);
        }

        static Request Request()
        {
            return new Request(CancellationToken.None, 10000);
        }

        [MTAThread]
        static int Main(string[] args)
        {
            QuietWindow working = null, popup = null;
            IntPtr workingHandle = IntPtr.Zero, popupHandle = IntPtr.Zero;
            var ready = new ManualResetEvent(false);
            Exception uiError = null;
            var ui = new Thread(() =>
            {
                try
                {
                    using (working = new QuietWindow("Directory lifetime working window", 20))
                    using (popup = new QuietWindow("Directory lifetime transient window", 240))
                    {
                        working.Shown += (s, e) =>
                        {
                            popup.Show();
                            workingHandle = working.Handle;
                            popupHandle = popup.Handle;
                            ready.Set();
                        };
                        Application.Run(working);
                    }
                }
                catch (Exception error)
                {
                    uiError = error;
                    ready.Set();
                }
            });
            ui.IsBackground = true;
            ui.SetApartmentState(ApartmentState.STA);
            object result;
            int exit = 0;
            Native.Initialize();
            IntPtr foreground = Native.GetForegroundWindow();
            try
            {
                ui.Start();
                Assert(ready.WaitOne(5000) && uiError == null, "Fixture could not open");
                bool closeDuringCollection = false, injected = false;
                var reader = new NativeReadBackend("lifetime-acceptance", message =>
                {
                    if (closeDuringCollection && message == "directory surfaces collected")
                    {
                        closeDuringCollection = false;
                        working.Invoke((Action)(() => popup.Close()));
                        Assert(!Native.IsWindow(popupHandle), "Transient window did not close");
                        injected = true;
                    }
                });
                var windows = reader.Store.Catalog(Request(), new List<string>(), false);
                var root = windows.Single(w => w.Native.Handle == workingHandle);
                var transient = windows.Single(w => w.Native.Handle == popupHandle);
                var target = Wire.Obj("session", "lifetime-acceptance", "instance", root.Native.Selector, "rootWindow", root.Native.Ref, "surface", root.Native.Scope, "node", null, "capture", null);
                var query = Wire.Obj("kind", "surfaces", "application", root.Application, "includeAccessibility", false);
                closeDuringCollection = true;
                var directory = Wire.Object(reader.Inspect(target, query, Request()));
                Assert(injected, "Close was not interleaved with directory assembly");
                var rows = Wire.Array(directory["surfaces"]).Select(Wire.Object).ToArray();
                Assert(rows.Any(w => Equals(w["window"], root.Native.Ref)), "Live working window was lost");
                Assert(!rows.Any(w => Equals(w["window"], transient.Native.Ref)), "Closed transient was returned");
                Assert(Wire.Array(directory["stopReasons"]).Any(n => n.ToString().Contains("closed or was replaced")), "Missing coverage note");
                reader.Store.Validate(target, Request(), true);
                working.Invoke((Action)(() => working.Close()));
                bool refused = false;
                try
                {
                    reader.Store.Validate(target, Request(), true);
                }
                catch (Failure error)
                {
                    refused = error.Code == "stale_target";
                }

                Assert(refused, "Closed working window binding must still be refused");
                Assert(Native.GetForegroundWindow() == foreground, "Read test changed foreground");
                result = Wire.Obj("status", "passed", "directory", directory, "closedRootRefused", refused, "foregroundUnchanged", true);
            }
            catch (Exception error)
            {
                exit = 1;
                result = Wire.Obj("status", "failed", "error", error.ToString());
            }
            finally
            {
                if (working != null && !working.IsDisposed)
                    try
                    {
                        working.Invoke((Action)(() => working.Close()));
                    }
                    catch
                    {
                    }

                if (ui.IsAlive)
                    ui.Join(3000);
            }

            File.WriteAllText(args[0], Wire.Json(result), new UTF8Encoding(false));
            return exit;
        }
    }
}
