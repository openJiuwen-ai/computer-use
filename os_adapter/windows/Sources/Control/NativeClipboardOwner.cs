// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    // One transaction, one message-only owner. Completion sources remain safe
    // after caller timeout; callbacks never touch an already-disposed wait.
    internal sealed class NativeClipboardOwner : IDisposable
    {
        const int RunWork = 0x8000 + 127, StopOwner = RunWork + 1, WorkTimeoutMs = 5000;
        readonly Queue<Action> work = new Queue<Action>();
        readonly object invokeGate = new object();
        readonly Action<int, IntPtr> callback;
        readonly Action release;
        readonly TaskCompletionSource<bool> ready = new TaskCompletionSource<bool>();
        readonly Thread thread;
        ApplicationContext context;
        OwnerWindow window;
        Exception startup;
        volatile bool closing, poisoned;
        bool disposed;
        internal IntPtr Handle { get; private set; }
        sealed class OwnerWindow : NativeWindow
        {
            readonly NativeClipboardOwner owner;
            internal OwnerWindow(NativeClipboardOwner owner)
            {
                this.owner = owner;
                CreateHandle(
                    new CreateParams { Caption = "ComputerUse clipboard transaction", Parent = new IntPtr(-3) });
            }
            protected override void WndProc(ref Message m)
            {
                if (m.Msg == StopOwner)
                {
                    owner.context.ExitThread();
                    return;
                }
                if (m.Msg == RunWork)
                {
                    Action next = null;
                    lock (owner.work)
                    {
                        if (owner.work.Count != 0)
                            next = owner.work.Dequeue();
                    }
                    if (next != null)
                        next();
                    return;
                }
                try
                {
                    owner.callback(m.Msg, m.WParam);
                }
                catch
                {
                    owner.poisoned = true;
                }
                base.WndProc(ref m);
            }
        }
        internal NativeClipboardOwner(Action<int, IntPtr> callback, Action release)
        {
            this.callback = callback;
            this.release = release;
            thread = new Thread(() =>
                                {
                                    try
                                    {
                                        context = new ApplicationContext();
                                        window = new OwnerWindow(this);
                                        Handle = window.Handle;
                                        ready.TrySetResult(true);
                                        if (!closing)
                                            Application.Run(context);
                                    }
                                    catch (Exception error)
                                    {
                                        startup = error;
                                        ready.TrySetResult(false);
                                    }
                                    finally
                                    {
                                        try
                                        {
                                            if (window != null && window.Handle != IntPtr.Zero)
                                                window.DestroyHandle();
                                        }
                                        catch
                                        {
                                            poisoned = true;
                                        }
                                        try
                                        {
                                            release();
                                        }
                                        catch
                                        {
                                            poisoned = true;
                                        }
                                        if (context != null)
                                            context.Dispose();
                                    }
                                }) { IsBackground = true, Name = "ComputerUse clipboard owner" };
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
            if (!ready.Task.Wait(2000))
            {
                closing = true;
                if (Handle != IntPtr.Zero)
                    PostMessage(Handle, StopOwner, IntPtr.Zero, IntPtr.Zero);
                if (!thread.Join(1000))
                    throw new NativeClipboardCleanupFailure(
                        "clipboard owner startup and thread cleanup could not be confirmed");
                throw new Failure("input_unavailable", "clipboard owner startup exceeded its bound; no input sent");
            }
            if (startup != null || !ready.Task.Result)
            {
                if (!thread.Join(1000))
                    throw new NativeClipboardCleanupFailure(
                        "clipboard owner initialization failed and its thread did not stop");
                throw new Failure("input_unavailable", "clipboard owner thread could not start: " +
                                                           (startup == null ? "unknown failure" : startup.Message));
            }
        }
        internal T Invoke<T>(Func<T> call)
        {
            lock (invokeGate)
            {
                if (disposed || closing || poisoned || !thread.IsAlive)
                    throw new Failure("execution_unknown",
                                      "clipboard owner thread is unavailable; resource state requires cleanup");
                T result = default(T);
                Exception failure = null;
                var done = new TaskCompletionSource<bool>();
                lock (work)
                {
                    work.Enqueue(() =>
                                 {
                                     try
                                     {
                                         result = call();
                                     }
                                     catch (Exception error)
                                     {
                                         failure = error;
                                     }
                                     finally
                                     {
                                         done.TrySetResult(true);
                                     }
                                 });
                    if (!PostMessage(Handle, RunWork, IntPtr.Zero, IntPtr.Zero))
                    {
                        // invokeGate guarantees at most one queued command.
                        work.Dequeue();
                        poisoned = true;
                        throw new Failure("execution_unknown", "clipboard owner work could not be queued");
                    }
                }
                double until = Wire.Now + WorkTimeoutMs;
                while (!done.Task.Wait(20))
                {
                    if (!thread.IsAlive || Wire.Now >= until)
                    {
                        poisoned = true;
                        throw new Failure(
                            "execution_unknown",
                            "clipboard resource command did not finish within its bound; it may still finish and was not replayed");
                    }
                }
                if (failure != null)
                    throw failure;
                return result;
            }
        }
        public void Dispose()
        {
            lock (invokeGate)
            {
                if (disposed)
                    return;
                disposed = true;
                closing = true;
                if (thread.IsAlive && Handle != IntPtr.Zero)
                    PostMessage(Handle, StopOwner, IntPtr.Zero, IntPtr.Zero);
            }
            if (!thread.Join(1000))
                throw new Failure("execution_unknown", "clipboard owner did not stop within the cleanup bound");
            if (poisoned)
                throw new Failure("execution_unknown", "clipboard owner resource failure was retained during cleanup");
        }
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool PostMessage(IntPtr window, int message, IntPtr w, IntPtr l);
    }
}
