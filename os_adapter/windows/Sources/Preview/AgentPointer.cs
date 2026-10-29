// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Linq;
using System.Threading;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    // Per connection, outside the replaceable UIA worker. Observed rectangles
    // are display hints, never input authority or a reason to change a route.
    internal sealed class AgentPointer : IDisposable
    {
        readonly bool enabled = Environment.GetEnvironmentVariable("COMPUTER_USE_POINTER") != "0";
        readonly object gate = new object ();
        readonly Dictionary<string, Window> windows = new Dictionary<string, Window>();
        readonly Dictionary<string, Location> nodes = new Dictionary<string, Location>();
        readonly Queue<string> nodeOrder = new Queue<string>();
        readonly Dictionary<string, PointF> history = new Dictionary<string, PointF>();
        sealed class Location
        {
            internal Window Window;
            internal PointF? Offset;
        }

        Window target;
        PointF? offset;
        string operation, intent;
        Dictionary<string, object> original;
        long version, activity, pulse, lastNotice;
        int pulseCount = 1, captureDepth;
        double beganAt, endedAt, pulseAt;
        bool active, dragging, selectionAimed;
        volatile bool stopping;
        volatile AgentPointerWindow overlay;
        internal AgentPointer()
        {
            if (!enabled)
                return;
            var thread = new Thread(RenderLoop)
            {
                IsBackground = true,
                Name = "Agent pointer display"
            };
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
        }

        internal static double Now
        {
            get
            {
                return (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
            }
        }

        internal void Checkpoint(Dictionary<string, Dictionary<string, object>> catalogue, Dictionary<string, long> generations)
        {
            if (!enabled || stopping)
                return;
            windows.Clear();
            foreach (var entry in catalogue)
            {
                var item = entry.Value;
                windows[entry.Key] = new Window
                {
                    Handle = new IntPtr(Convert.ToInt64(item["handle"])),
                    Pid = Convert.ToUInt32(item["pid"]),
                    Started = Int64.Parse((string)item["started"]),
                    Generation = generations[entry.Key],
                    Scope = entry.Key
                };
            }
        }

        Window Selected(Dictionary<string, object> binding)
        {
            Window window;
            string surface = binding["surface"] as string;
            return surface != null && windows.TryGetValue(surface, out window) ? window : null;
        }

        static bool Visible(Window window)
        {
            try
            {
                window.Validate();
                return NativeWindowDiscovery.Visible(window.Handle) && !Native.IsIconic(window.Handle);
            }
            catch
            {
                return false;
            }
        }

        internal void Observe(string method, Dictionary<string, object> parameters, object raw)
        {
            if (!enabled || stopping)
                return;
            var result = Wire.Object(raw);
            if (method == "inspect" && Equals(result["kind"], "observation"))
            {
                var details = Wire.Array(Wire.Object(result["read"])["details"]).Select(Wire.Array);
                var scope = details.FirstOrDefault(item => Equals(item[0], "surface"));
                Window window;
                string surface = scope == null ? null : Wire.Object(scope[1])["data"] as string;
                if (surface == null || !windows.TryGetValue(surface, out window))
                    return;
                Rectangle frame;
                try
                {
                    frame = Native.Bounds(window.Handle);
                }
                catch
                {
                    return;
                }

                foreach (object value in Wire.Array(result["nodes"]))
                {
                    var row = Wire.Object(value);
                    if (!Equals(row["kind"], "element") || row["properties"] == null)
                        continue;
                    var properties = Wire.Array(row["properties"]).Select(Wire.Object).ToArray();
                    Func<string, object> field = name =>
                    {
                        var property = properties.FirstOrDefault(p => Equals(Wire.Array(p["key"])[0], "windows.uia") && Equals(Wire.Array(p["key"])[1], name));
                        return property == null || property["value"] == null ? null : Wire.Object(property["value"])["data"];
                    };
                    PointF? point = null;
                    object rectangle = field("BoundingRectangle"), offscreen = field("IsOffscreen");
                    string id = (string)row["id"];
                    // Narrow matching/preparation reads omit geometry. Their
                    // omission is not a new observation of an unknown rectangle.
                    if (!properties.Any(p => Equals(Wire.Array(p["key"])[1], "BoundingRectangle")) && !Equals(offscreen, true))
                    {
                        if (nodes.ContainsKey(id))
                            continue;
                    }

                    if (!Equals(offscreen, true) && rectangle != null)
                    {
                        var rect = Wire.Array(Wire.Object(rectangle)["fields"]).Select(Wire.Array).ToDictionary(item => (string)item[0], item => Wire.Object(item[1])["data"]);
                        double x = Convert.ToDouble(rect["x"]), y = Convert.ToDouble(rect["y"]), w = Convert.ToDouble(rect["width"]), h = Convert.ToDouble(rect["height"]);
                        if (new[]
                        {
                            x,
                            y,
                            w,
                            h
                        }.All(v => !Double.IsNaN(v) && !Double.IsInfinity(v)) && w > 0 && h > 0)
                        {
                            double left = Math.Max(frame.Left, x), top = Math.Max(frame.Top, y), right = Math.Min(frame.Right, x + w), bottom = Math.Min(frame.Bottom, y + h);
                            if (right > left && bottom > top)
                                point = new PointF((float)((left + right) / 2 - frame.X), (float)((top + bottom) / 2 - frame.Y));
                        }
                    }

                    if (!nodes.ContainsKey(id))
                        nodeOrder.Enqueue(id);
                    nodes[id] = new Location
                    {
                        Window = window,
                        Offset = point
                    };
                    while (nodes.Count > 8192)
                        nodes.Remove(nodeOrder.Dequeue());
                }

                ObserveWindow(window);
            }
            else if (method == "capture" && Equals(result["kind"], "capture"))
                ObserveWindow(Selected(Wire.Object(result["binding"])));
            else if (method == "inspect" && Equals(result["kind"], "inputContext") && active && original != null && original["node"] == null && !selectionAimed)
            {
                var binding = Wire.Object(result["binding"]);
                Location location;
                // Lazy Element paths bind their addressed node after begin.
                // Pin that first resolved element, before any editor preparation.
                string selected = binding["node"] as string;
                if (selected != null && binding["surface"] as string == original["surface"] as string)
                {
                    selectionAimed = true;
                    if (nodes.TryGetValue(selected, out location))
                        Aim(location.Window, location.Offset, intent != "scroll" && intent != "key", 1);
                    else
                        Aim(Selected(binding), null, false, 1);
                    return;
                }

                if (!new[]
                {
                    "insert",
                    "paste",
                    "replace",
                    "focus"
                }.Contains(intent))
                    return;
                var input = Wire.Object(result["input"]);
                string receiver = input["editor"] as string ?? input["focusedNode"] as string;
                if (receiver != null && nodes.TryGetValue(receiver, out location) && location.Window.Scope == original["surface"] as string)
                {
                    selectionAimed = true;
                    Aim(location.Window, location.Offset, true, 1);
                }
            }
        }

        void ObserveWindow(Window window)
        {
            if (window == null)
                return;
            lock (gate)
            {
                // Read another visible window without moving the last target.
                if (offset.HasValue && (target == null || Visible(target)))
                    return;
                if (target != null && Visible(target))
                    return;
                PointF point;
                if (!history.TryGetValue(window.Scope, out point))
                {
                    Rectangle frame;
                    try
                    {
                        frame = Native.Bounds(window.Handle);
                    }
                    catch
                    {
                        return;
                    }

                    point = new PointF(frame.Width / 2f, frame.Height / 2f);
                }

                SetTarget(window, point);
            }
        }

        void SetTarget(Window window, PointF? point)
        {
            target = window;
            offset = point;
            version++;
            if (window != null && point.HasValue)
            {
                if (history.Count >= 4096 && !history.ContainsKey(window.Scope))
                    history.Remove(history.Keys.First());
                history[window.Scope] = point.Value;
            }
        }

        void Aim(Window window, PointF? point, bool press, int count, bool drag = false)
        {
            lock (gate)
            {
                if (stopping)
                    return;
                SetTarget(window, point);
                dragging = drag;
                if (press && point.HasValue)
                {
                    pulse++;
                    pulseCount = count;
                    pulseAt = Now;
                }
            }
        }

        internal void Begin(string id, Dictionary<string, object> binding, Dictionary<string, object> requestIntent)
        {
            if (!enabled || stopping)
                return;
            lock (gate)
            {
                operation = id;
                original = new Dictionary<string, object>(binding);
                intent = (string)requestIntent["kind"];
                active = true;
                dragging = false;
                selectionAimed = false;
                beganAt = Now;
                activity++;
                lastNotice = 0;
            }

            if (binding["capture"] != null)
                return; // The pointer unit provides the actual mapped physical position.
            if (binding["node"] != null)
            {
                Location location;
                bool press = new[]
                {
                    "click",
                    "action",
                    "openMenu",
                    "focus",
                    "insert",
                    "replace",
                    "paste"
                }.Contains(intent);
                if (nodes.TryGetValue((string)binding["node"], out location) && location.Window.Scope == binding["surface"] as string)
                    Aim(location.Window, location.Offset, press, 1);
                else
                    Aim(Selected(binding), null, false, 1);
            }
            else if (new[]
            {
                "insert",
                "replace",
                "paste",
                "focus"
            }.Contains(intent))
                Aim(Selected(binding), null, false, 1);
        }

        internal void Notice(object raw)
        {
            if (!enabled || stopping || raw == null)
                return;
            var notice = Wire.Object(raw);
            lock (gate)
            {
                long sequence = Convert.ToInt64(notice["sequence"]);
                var binding = Wire.Object(notice["target"]);
                if (!active || !Equals(notice["operation"], operation) || sequence <= lastNotice || !NativeWriteOperation.Same(binding, original))
                    return;
                lastNotice = sequence;
                var point = new PointF(Convert.ToSingle(notice["x"]), Convert.ToSingle(notice["y"]));
                var window = Selected(binding);
                if (binding["surface"] != null && window == null)
                {
                    SetTarget(null, null);
                    return;
                }

                if (window != null)
                {
                    Rectangle frame;
                    try
                    {
                        frame = Native.Bounds(window.Handle);
                    }
                    catch
                    {
                        SetTarget(window, null);
                        return;
                    }

                    point = new PointF(point.X - frame.X, point.Y - frame.Y);
                }

                Aim(window, point, Equals(notice["pulse"], true), Convert.ToInt32(notice["count"]), Equals(notice["dragging"], true));
            }
        }

        internal void End(bool success = true)
        {
            if (!enabled || stopping)
                return;
            lock (gate)
            {
                if (!active)
                    return;
                active = false;
                dragging = false;
                endedAt = Now;
                operation = null;
                original = null;
                if (!success)
                {
                    pulseAt = 0;
                    pulse++;
                }
            }
        }

        internal void Outcome(bool success)
        {
            if (!enabled || stopping || success)
                return;
            // An unavailable unit can still be followed by legitimate native
            // preparation within this same operation. Only finish/expiry ends
            // activity; failure cancels feedback without ending the lifecycle.
            lock (gate)
            {
                dragging = false;
                pulseAt = 0;
                pulse++;
            }
        }

        internal IDisposable IsolateCapture()
        {
            Interlocked.Increment(ref captureDepth);
            IDisposable isolation;
            try
            {
                isolation = AgentPointerWindow.IsolateCaptures();
            }
            catch
            {
                Interlocked.Decrement(ref captureDepth);
                throw;
            }

            return new CaptureLease(this, isolation);
        }

        sealed class CaptureLease : IDisposable
        {
            AgentPointer owner;
            IDisposable isolation;
            internal CaptureLease(AgentPointer owner, IDisposable isolation)
            {
                this.owner = owner;
                this.isolation = isolation;
            }

            public void Dispose()
            {
                if (owner == null)
                    return;
                try
                {
                    isolation.Dispose();
                }
                finally
                {
                    Interlocked.Decrement(ref owner.captureDepth);
                    owner = null;
                }
            }
        }

        void RenderLoop()
        {
            AgentPointerWindow window = null;
            System.Windows.Forms.Timer timer = null;
            try
            {
                window = new AgentPointerWindow();
                overlay = window;
                var clock = new AgentPointerVisualState();
                bool initialized = false, wasActive = false;
                long seenVersion = -1, seenActivity = 0, seenPulse = 0;
                Window current = null;
                PointF from = PointF.Empty, goal = PointF.Empty;
                double movedAt = 0, moveDuration = 0, appearedAt = 0, pressedAt = 0;
                int count = 1;
                bool reduced = AgentPointerWindow.ReducedMotion();
                timer = new System.Windows.Forms.Timer
                {
                    Interval = 16
                };
                timer.Tick += (sender, args) =>
                {
                    if (stopping)
                    {
                        window.Dispose();
                        timer.Stop();
                        Application.ExitThread();
                        return;
                    }

                    try
                    {
                        Window next;
                        PointF? nextOffset;
                        long revision, action, feedback;
                        double begin, end, press;
                        bool busy, drag;
                        lock (gate)
                        {
                            next = target;
                            nextOffset = offset;
                            revision = version;
                            action = activity;
                            feedback = pulse;
                            begin = beganAt;
                            end = endedAt;
                            press = pulseAt;
                            busy = active;
                            drag = dragging;
                            count = pulseCount;
                        }

                        double now = Now;
                        if (action != seenActivity)
                        {
                            clock.Begin(begin);
                            wasActive = true;
                            seenActivity = action;
                        }

                        if (wasActive && !busy)
                        {
                            clock.End(end);
                            wasActive = false;
                        }

                        if (revision != seenVersion)
                        {
                            if (nextOffset.HasValue)
                            {
                                bool same = initialized && ((current == null && next == null) || current != null && next != null && current.Scope == next.Scope);
                                from = same ? Interpolate(from, goal, reduced ? 1 : AgentPointerVisualState.Ease(Fraction(now, movedAt, moveDuration))) : nextOffset.Value;
                                goal = nextOffset.Value;
                                moveDuration = same ? AgentPointerVisualState.MoveDuration(from, goal, drag) : 0;
                                movedAt = now;
                                if (!same)
                                    appearedAt = now;
                                if (!initialized)
                                {
                                    clock.Show(now);
                                    initialized = true;
                                }
                            }

                            current = next;
                            seenVersion = revision;
                        }

                        if (feedback != seenPulse)
                        {
                            pressedAt = press;
                            seenPulse = feedback;
                        }

                        double opacity = clock.Opacity(now); // Reads/capture/visibility never restart this clock.
                        if (!nextOffset.HasValue || Volatile.Read(ref captureDepth) > 0 || current != null && !Visible(current))
                        {
                            window.Hide();
                            return;
                        }

                        var local = Interpolate(from, goal, reduced ? 1 : AgentPointerVisualState.Ease(Fraction(now, movedAt, moveDuration)));
                        Rectangle frame = current == null ? SystemInformation.VirtualScreen : Native.Bounds(current.Handle);
                        var point = current == null ? local : new PointF(frame.X + local.X, frame.Y + local.Y);
                        if (!frame.Contains(Point.Round(point)))
                        {
                            window.Hide();
                            return;
                        }

                        float scale = AgentPointerWindow.Scale(point);
                        var bounds = AgentPointerArtwork.Bounds;
                        var position = new Point((int)Math.Floor(point.X + bounds.Left * scale), (int)Math.Floor(point.Y + bounds.Top * scale));
                        int width = (int)Math.Ceiling(point.X + bounds.Right * scale - position.X), height = (int)Math.Ceiling(point.Y + bounds.Bottom * scale - position.Y);
                        using (var image = new Bitmap(width, height, PixelFormat.Format32bppPArgb))
                        using (var graphics = Graphics.FromImage(image))
                        {
                            graphics.SmoothingMode = SmoothingMode.AntiAlias;
                            graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                            graphics.SetClip(new Rectangle(frame.X - position.X, frame.Y - position.Y, frame.Width, frame.Height));
                            graphics.TranslateTransform(point.X - position.X, point.Y - position.Y);
                            graphics.ScaleTransform(scale, scale);
                            double elapsed = now - pressedAt;
                            if (!reduced && pressedAt > 0 && elapsed < AgentPointerAssets.RippleDuration)
                            {
                                double f = elapsed / AgentPointerAssets.RippleDuration, radius = AgentPointerAssets.RippleFromRadius + (AgentPointerAssets.RippleToRadius - AgentPointerAssets.RippleFromRadius) * f;
                                using (var pen = new Pen(Color.FromArgb((int)(255 * AgentPointerAssets.RippleFromOpacity * (1 - f)), AgentPointerAssets.RippleColor), (float)AgentPointerAssets.RippleWidth))
                                    graphics.DrawEllipse(pen, (float)-radius, (float)-radius, (float)(radius * 2), (float)(radius * 2));
                            }

                            double pressDuration = count == 2 ? AgentPointerAssets.PressDoubleDuration : AgentPointerAssets.PressDuration;
                            float bodyScale = (float)(drag ? AgentPointerAssets.DragScale : reduced ? 1 : AgentPointerVisualState.Press(pressedAt > 0 ? elapsed / pressDuration : 1, count));
                            graphics.ScaleTransform(bodyScale, bodyScale);
                            graphics.TranslateTransform(-AgentPointerAssets.Hotspot.X, -AgentPointerAssets.Hotspot.Y);
                            AgentPointerAssets.Draw(graphics);
                            if (reduced && pressedAt > 0 && elapsed < AgentPointerAssets.ReducedMotionFlash)
                                using (var path = AgentPointerAssets.Path())
                                using (var brush = new SolidBrush(Color.FromArgb(90, AgentPointerAssets.ReducedMotionPressedColor)))
                                    graphics.FillPath(brush, path);
                            window.Present(image, position, (byte)Math.Round(255 * opacity * (reduced ? 1 : Fraction(now, appearedAt, AgentPointerAssets.Appear))), current == null ? IntPtr.Zero : current.Handle);
                        }
                    }
                    catch
                    {
                        window.Hide();
                    } // Display failure cannot change input or observation results.
                };
                timer.Start();
                Application.Run();
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("agent pointer display unavailable: " + error.Message);
            }
            finally
            {
                if (timer != null)
                    timer.Dispose();
                if (window != null)
                    window.Dispose();
                overlay = null;
            }
        }

        static double Fraction(double now, double at, double duration)
        {
            return duration <= 0 ? 1 : Math.Max(0, Math.Min(1, (now - at) / duration));
        }

        static PointF Interpolate(PointF a, PointF b, double fraction)
        {
            return new PointF((float)(a.X + (b.X - a.X) * fraction), (float)(a.Y + (b.Y - a.Y) * fraction));
        }

        public void Dispose()
        {
            stopping = true;
            var window = overlay;
            if (window != null)
                window.Shutdown();
        }
    }
}
