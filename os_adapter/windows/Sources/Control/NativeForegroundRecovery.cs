// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    // A last preparation step for an exclusive desktop, never a replay of the
    // requested input. Normal activation must already have failed. The shell
    // owns its menu; only a verified surface opened here receives Escape.
    internal static class NativeForegroundRecovery
    {
        internal sealed class Surface
        {
            internal IntPtr Handle;
            internal uint Pid, Thread, Session;
            internal long Started, Generation;
            internal string Image;
            internal bool Same(Surface other)
            {
                return other != null && Handle == other.Handle && Pid == other.Pid && Thread == other.Thread && Started == other.Started && Generation == other.Generation && Session == other.Session && String.Equals(Image, other.Image, StringComparison.OrdinalIgnoreCase);
            }
        }

        internal abstract class Host
        {
            internal abstract double Now { get; }

            internal abstract void Guard(bool deep);
            internal abstract Surface Foreground();
            // 0 means confirmed hidden/closed; -1 means unknown.
            internal abstract int Visibility(Surface surface);
            internal abstract bool Idle(Surface surface, bool requireFocus);
            internal abstract Surface Shell();
            internal abstract bool TrustedMenu(Surface surface);
            internal abstract bool ExistingMenu();
            internal abstract bool Open(Surface shell, int timeout);
            internal abstract uint Escape(Surface menu);
            internal abstract void ReleaseEscape();
            internal abstract void Activate();
            internal abstract bool TargetForeground();
            internal abstract void Wait();
        }

        internal static bool Try(bool exclusive, Request r, Host host)
        {
            if (!exclusive || r.Dispatched || r.ForegroundRecoveryAttempted)
                return false;
            r.Check();
            host.Guard(false);
            var before = host.Foreground();
            if (before == null || host.Visibility(before) != 0 || !host.Idle(before, false))
                return false;
            double started = host.Now;
            double until = Math.Min(Math.Min(r.Deadline, r.ObservationDeadline), started + 1800);
            Action<bool> guard = deep =>
            {
                r.Check();
                if (host.Now >= until)
                    throw Unavailable("the bounded system-menu recovery did not finish");
                host.Guard(deep);
            };
            var shell = host.Shell();
            if (shell == null || host.ExistingMenu())
                return false;
            guard(true);
            if (!before.Same(host.Foreground()) || host.Visibility(before) != 0 || !host.Idle(before, false))
                return false;
            r.ForegroundRecoveryAttempted = true;
            r.Effect = true;
            var shellEffect = r.ClickPreparation == null ? null : r.ClickPreparation.Preparing("shellMenu");
            NativePreparationEffect escapeEffect = null;
            if (r.ClickPreparation != null)
                r.ClickPreparation.RequireObservation();
            string stage = "open", failure = null;
            uint accepted = 0;
            bool recovered = false;
            try
            {
                int timeout = Math.Max(1, (int)Math.Min(600, until - host.Now));
                if (!host.Open(shell, timeout))
                    throw Unavailable("the system-menu request did not acknowledge within its budget");
                if (shellEffect != null)
                    shellEffect.Dispatch = "dispatched";
                stage = "await_menu";
                Surface menu = null;
                while (menu == null)
                {
                    guard(false);
                    var current = host.Foreground();
                    if (current != null && !before.Same(current))
                    {
                        if (!host.TrustedMenu(current))
                            throw Unavailable("foreground changed to an unconfirmed window during system-menu preparation; no Escape sent");
                        if (host.Visibility(current) == 1 && host.Idle(current, true))
                            menu = current;
                    }

                    if (menu == null)
                        host.Wait();
                }

                stage = "escape";
                guard(true);
                if (!menu.Same(host.Foreground()) || host.Visibility(menu) != 1 || !host.Idle(menu, true))
                    throw Unavailable("the confirmed system-menu recipient changed; no Escape sent");
                // The host publishes one Escape down/up batch. These are
                // preparation effects, not the original content/pointer input.
                escapeEffect = r.ClickPreparation == null ? null : r.ClickPreparation.Preparing("escape");
                if (escapeEffect != null)
                    escapeEffect.Expected = 2;
                try
                {
                    accepted = host.Escape(menu);
                }
                catch
                {
                    if (escapeEffect != null)
                    {
                        try
                        {
                            host.ReleaseEscape();
                        }
                        catch
                        {
                            escapeEffect.Cleanup = "failed";
                        }
                    }

                    throw;
                }

                if (escapeEffect != null)
                {
                    escapeEffect.Accepted = (int)accepted;
                    escapeEffect.Dispatch = accepted == 0 ? "notDispatched" : accepted == 2 ? "dispatched" : "partial";
                }

                double escapeAt = host.Now;
                if (accepted == 1)
                {
                    try
                    {
                        host.ReleaseEscape();
                    }
                    catch
                    {
                        if (escapeEffect != null)
                            escapeEffect.Cleanup = "failed";
                        throw;
                    }
                }

                if (accepted != 2)
                    throw Unavailable("system-menu Escape accepted " + accepted + "/2 events; preparation was not replayed");
                stage = "await_closed";
                while (host.Visibility(menu) != 0)
                {
                    guard(false);
                    host.Wait();
                }

                // Cloaking can precede the shell's delayed owner restoration.
                // Do not mistake that animation's transient foreground for a
                // prepared receiver. Use the same bounded paint grace as input
                // observation, without another Escape or target activation.
                stage = "settle_closed";
                while (host.Now - escapeAt < 500)
                {
                    guard(false);
                    if (host.Visibility(menu) != 0)
                        throw Unavailable("the system menu became visible again while closing");
                    host.Wait();
                }

                stage = "activate_target";
                guard(true);
                host.Activate();
                double stable = -1;
                while (stable < 0 || host.Now - stable < 100)
                {
                    guard(false);
                    if (!host.TargetForeground())
                        stable = -1;
                    else if (stable < 0)
                        stable = host.Now;
                    host.Wait();
                }

                guard(true);
                if (!host.TargetForeground())
                    throw Unavailable("the original target lost foreground during final validation");
                if (shellEffect != null)
                    shellEffect.Observation = "confirmed";
                if (escapeEffect != null)
                    escapeEffect.Observation = "confirmed";
                recovered = true;
                return true;
            }
            catch (Exception error)
            {
                failure = error.GetType().Name + ": " + error.Message;
                throw;
            }
            finally
            {
                Surface current = null;
                try
                {
                    current = host.Foreground();
                }
                catch (Exception)
                {
                }

                try
                {
                    Console.Error.WriteLine(Wire.Json(Wire.Obj("kind", "foreground_shell_recovery", "stage", stage, "recovered", recovered, "preparationEventsAccepted", accepted, "requestedInputDispatched", r.Dispatched, "elapsedMs", host.Now - started, "failure", failure, "foreground", current == null ? 0 : current.Handle.ToInt64())));
                }
                catch (IOException)
                {
                }
            }
        }

        static Failure Unavailable(string detail)
        {
            return new Failure("input_unavailable", detail + "; no requested input sent", "read the current desktop and original target before continuing; a system menu opened during preparation may still be visible");
        }

        internal static bool Restore(Window target, Request r, Action validate)
        {
            return Try(true, r, new Live(target, r, validate));
        }

        internal static bool PreferShell(Surface foreground)
        {
            return foreground != null && foreground.Session == UInt32.MaxValue && foreground.Started == 0 && foreground.Image == null;
        }

        internal static bool RestoreBeforeFocus(Window target, Request r, Action validate)
        {
            var host = new Live(target, r, validate);
            // UIA SetFocus took two seconds in this measured refusal, even
            // with TransactionTimeout=250. The hidden foreground is never an
            // input recipient. Only the independently verified shell/menu can
            // receive preparation, under Try's full exclusive/identity guards.
            return PreferShell(host.Foreground()) && Try(true, r, host);
        }

        sealed class Live : Host
        {
            [DllImport("user32.dll")]
            static extern IntPtr GetShellWindow();
            [DllImport("kernel32.dll")]
            static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
            [DllImport("kernel32.dll")]
            static extern bool CloseHandle(IntPtr process);
            [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
            static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder image, ref uint size);
            [DllImport("kernel32.dll")]
            static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
            [DllImport("kernel32.dll")]
            static extern bool ProcessIdToSessionId(uint pid, out uint session);
            [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
            static extern uint GetSystemWindowsDirectory(StringBuilder path, uint size);
            readonly Window target;
            readonly Request request;
            readonly Action validate;
            readonly uint session;
            readonly string windows;
            readonly HashSet<IntPtr> visibleBefore = new HashSet<IntPtr>();
            internal Live(Window target, Request request, Action validate)
            {
                this.target = target;
                this.request = request;
                this.validate = validate;
                using (var own = Process.GetCurrentProcess())
                    session = (uint)own.SessionId;
                var path = new StringBuilder(32768);
                uint length = GetSystemWindowsDirectory(path, (uint)path.Capacity);
                windows = length > 0 && length < path.Capacity ? path.ToString() : null;
            }

            internal override double Now
            {
                get
                {
                    return Wire.Now;
                }
            }

            internal override void Guard(bool deep)
            {
                request.Check();
                target.Validate();
                // GetForegroundWindow may be NULL while a window loses
                // activation. This owned shell transition still needs the
                // same unlocked input desktop, not a continuously nonzero HWND.
                if (!Native.Interactive(false))
                    throw new Failure("desktop_unavailable", "system-menu preparation lost the interactive desktop; no requested input sent");
                NativeDesktopState.RequireInput();
                Native.Released();
                if (Native.NoActivate(target.Handle) || !Native.IsWindowVisible(target.Handle) || Native.IsIconic(target.Handle) || !Native.IsWindowEnabled(target.Handle))
                    throw Unavailable("the original target became unavailable during preparation");
                if (deep && validate != null)
                    validate();
            }

            Surface Read(IntPtr handle)
            {
                if (handle == IntPtr.Zero || !Native.IsWindow(handle))
                    return null;
                uint pid, processSession;
                uint thread = Native.GetWindowThreadProcessId(handle, out pid);
                if (thread == 0)
                    return null;
                // The blocking foreground may deny process queries (measured
                // with GameInput). HWND/thread lifetime still identifies that
                // hidden window. Unknown session/image never establishes trust
                // for the Explorer shell or the recipient of Escape.
                if (!ProcessIdToSessionId(pid, out processSession))
                    processSession = UInt32.MaxValue;
                var result = new Surface
                {
                    Handle = handle,
                    Pid = pid,
                    Thread = thread,
                    Session = processSession,
                    Generation = Native.Generation(handle)
                };
                IntPtr process = OpenProcess(0x1000, false, pid);
                if (process != IntPtr.Zero)
                    try
                    {
                        long created, exited, kernel, user;
                        var image = new StringBuilder(32768);
                        uint size = (uint)image.Capacity;
                        if (GetProcessTimes(process, out created, out exited, out kernel, out user) && QueryFullProcessImageName(process, 0, image, ref size))
                        {
                            result.Started = created;
                            result.Image = image.ToString();
                        }
                    }
                    finally
                    {
                        CloseHandle(process);
                    }

                return result;
            }

            internal override Surface Foreground()
            {
                return Read(Native.GetForegroundWindow());
            }

            internal override int Visibility(Surface surface)
            {
                if (!Native.IsWindow(surface.Handle))
                    return 0;
                // A reused HWND cannot stand in for the surface opened here.
                if (!surface.Same(Read(surface.Handle)))
                    throw Unavailable("a preparation window changed identity");
                if (!Native.IsWindowVisible(surface.Handle))
                    return 0;
                int cloaked;
                if (Native.DwmGetWindowAttribute(surface.Handle, 14, out cloaked, 4) != 0)
                    return -1;
                return cloaked == 0 ? 1 : 0;
            }

            internal override bool Idle(Surface surface, bool requireFocus)
            {
                if (!surface.Same(Read(surface.Handle)))
                    return false;
                var queue = new Native.GUI
                {
                    Size = Marshal.SizeOf(typeof(Native.GUI))
                };
                if (!Native.GetGUIThreadInfo(surface.Thread, ref queue) || queue.Capture != IntPtr.Zero || queue.MenuOwner != IntPtr.Zero || queue.MoveSize != IntPtr.Zero || (queue.Flags & 0x1e) != 0)
                    return false;
                if (requireFocus && (Native.GetAsyncKeyState(27) & 0x8000) != 0)
                    return false;
                return !requireFocus || queue.Active == surface.Handle && queue.Focus != IntPtr.Zero && Native.Inside(surface.Handle, queue.Focus);
            }

            bool Image(Surface surface, string relative)
            {
                return windows != null && surface != null && surface.Session == session && surface.Started != 0 && String.Equals(surface.Image, Path.Combine(windows, relative), StringComparison.OrdinalIgnoreCase);
            }

            internal override Surface Shell()
            {
                var shell = Read(GetShellWindow());
                return Image(shell, "explorer.exe") && shell.Same(Read(GetShellWindow())) ? shell : null;
            }

            bool SystemMenu(Surface surface)
            {
                return Image(surface, @"SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\SearchHost.exe") || Image(surface, @"SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\StartMenuExperienceHost.exe") || Image(surface, @"SystemApps\Microsoft.Windows.Search_cw5n1h2txyewy\SearchApp.exe");
            }

            internal override bool TrustedMenu(Surface surface)
            {
                return SystemMenu(surface) && !visibleBefore.Contains(surface.Handle);
            }

            internal override bool ExistingMenu()
            {
                var seen = new HashSet<IntPtr>();
                int count = 0;
                bool present = false;
                Exception failed = null;
                Native.EnumWindows((handle, unused) =>
                {
                    try
                    {
                        request.Check();
                        if (++count > 1024)
                        {
                            present = true;
                            return false;
                        }

                        if (!Native.IsWindowVisible(handle) || !seen.Add(handle))
                            return true;
                        int cloaked;
                        if (Native.DwmGetWindowAttribute(handle, 14, out cloaked, 4) != 0 || cloaked == 0)
                            visibleBefore.Add(handle);
                        var surface = Read(handle);
                        if (surface != null && SystemMenu(surface) && Visibility(surface) != 0)
                        {
                            present = true;
                            return false;
                        }

                        return true;
                    }
                    catch (Exception error)
                    {
                        failed = error;
                        return false;
                    }
                }, IntPtr.Zero);
                if (failed != null)
                    throw failed;
                return present;
            }

            internal override bool Open(Surface shell, int timeout)
            {
                if (!shell.Same(Read(GetShellWindow())))
                    throw Unavailable("the system shell changed before preparation");
                UIntPtr reply;
                return Native.SendMessageTimeoutRaw(shell.Handle, 0x112, new IntPtr(0xf130), IntPtr.Zero, 0x23, (uint)timeout, out reply) != IntPtr.Zero;
            }

            internal override uint Escape(Surface menu)
            {
                Guard(false);
                NativeInputAccess.RequireWindow(menu.Handle, "system-menu preparation recipient");
                if (!menu.Same(Foreground()) || Visibility(menu) != 1 || !Idle(menu, true))
                    throw Unavailable("the verified system-menu recipient changed before Escape");
                var events = new[]
                {
                    Native.Key(27, false),
                    Native.Key(27, true)
                };
                return Native.SendInput(2, events, Marshal.SizeOf(typeof(Native.INPUT)));
            }

            internal override void ReleaseEscape()
            {
                Native.Release(Native.Key(27, true));
            }

            internal override void Activate()
            {
                Native.SetForegroundWindow(target.Handle);
            }

            internal override bool TargetForeground()
            {
                return Native.GetForegroundWindow() == target.Handle;
            }

            internal override void Wait()
            {
                request.Wait(20);
            }
        }
    }
}
