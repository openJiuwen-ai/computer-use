// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal struct NativePointerPoint
    {
        internal readonly int X, Y;
        internal NativePointerPoint(int x, int y)
        {
            X = x;
            Y = y;
        }
    }

    // Retain metadata, never PNG bytes or an inferred UIA target. Scope and
    // transforms cannot be refreshed implicitly after the model sees an image.
    // A selected execution may freeze a separate full-window translation;
    // that never mutates these pixels, scale, inset or capture-time bounds.
    internal sealed class NativePointerCapture
    {
        internal readonly Dictionary<string, object> Binding;
        internal readonly NativeCaptureGeometry Image, Canvas, WindowBounds;
        internal readonly string Layout;
        readonly NativeCaptureGeometry[] displays;
        internal NativePointerCapture(Dictionary<string, object> binding, NativeCaptureGeometry image, NativeCaptureGeometry canvas, string layout, NativeCaptureGeometry[] displays, NativeCaptureGeometry windowBounds = null)
        {
            Binding = new Dictionary<string, object>(binding);
            Image = image;
            Canvas = canvas;
            Layout = layout;
            WindowBounds = windowBounds ?? image;
            this.displays = (NativeCaptureGeometry[])displays.Clone();
        }

        internal Dictionary<string, object> Checkpoint()
        {
            return Wire.Obj("binding", Binding, "image", Image.Checkpoint(), "canvas", Canvas.Checkpoint(),
                            "layout", Layout, "displays", displays.Select(display => display.Checkpoint()).ToArray(),
                            "windowBounds", WindowBounds.Checkpoint());
        }

        internal static NativePointerCapture Restore(object raw, string session)
        {
            var value = Wire.Object(raw);
            Wire.Keys(value, "binding,image,canvas,layout,displays,windowBounds");
            var binding = NativeBinding.Resolve(value["binding"], session);
            var displays = Wire.Array(value["displays"]);
            if (binding["capture"] == null || displays.Length < 1 || displays.Length > 32)
                throw new Failure("invalid_request", "invalid captured pointer checkpoint");
            return new NativePointerCapture(binding, NativeCaptureGeometry.Restore(value["image"]),
                                            NativeCaptureGeometry.Restore(value["canvas"]), Wire.Str(value, "layout"),
                                            displays.Select(NativeCaptureGeometry.Restore).ToArray(),
                                            NativeCaptureGeometry.Restore(value["windowBounds"]));
        }

        internal static bool Contains(NativeCaptureGeometry box, NativePointerPoint p)
        {
            return p.X >= box.X && p.Y >= box.Y && (long)p.X < (long)box.X + box.NativeWidth && (long)p.Y < (long)box.Y + box.NativeHeight;
        }

        internal void RequirePoint(NativePointerPoint p)
        {
            if (!Contains(Canvas, p) || !displays.Any(d => Contains(d, p)))
                throw new Failure("invalid_request", "pointer trajectory leaves the captured displays");
        }

        internal void RequireCapturedPoint(NativePointerPoint point)
        {
            if (!Contains(Image, point))
                throw new Failure("invalid_request", "pointer trajectory leaves the retained image");
            // A window image may include pixels captured outside the desktop.
            // Their current projected coordinates determine screen reachability.
            if (Binding["surface"] == null)
                RequirePoint(point);
        }

        internal NativePointerPoint Point(double x, double y)
        {
            if (Double.IsNaN(x) || Double.IsNaN(y) || x < 0 || y < 0 || x >= Image.Width || y >= Image.Height)
                throw new Failure("invalid_request", "coordinates are outside the retained image");
            long physicalX = (long)Image.X + (int)Math.Floor(x * Image.NativeWidth / Image.Width);
            long physicalY = (long)Image.Y + (int)Math.Floor(y * Image.NativeHeight / Image.Height);
            if (physicalX < Int32.MinValue || physicalX > Int32.MaxValue ||
                physicalY < Int32.MinValue || physicalY > Int32.MaxValue)
                throw new Failure("invalid_request", "image coordinates exceed physical screen integer coordinates");
            var p = new NativePointerPoint((int)physicalX, (int)physicalY);
            RequireCapturedPoint(p);
            return p;
        }

        internal NativePointerPoint Normalize(NativePointerPoint point)
        {
            RequirePoint(point);
            return Normalize(point, Canvas);
        }

        internal static NativePointerPoint Normalize(NativePointerPoint point, NativeCaptureGeometry canvas)
        {
            // Aim at the center of the physical pixel's 16-bit interval. This
            // avoids rounding onto an adjacent pixel on scaled/multiple screens.
            return new NativePointerPoint((int)Math.Floor(((double)point.X - canvas.X + .5) * 65536 / canvas.NativeWidth), (int)Math.Floor(((double)point.Y - canvas.Y + .5) * 65536 / canvas.NativeHeight));
        }
    }

    internal sealed class NativePointerCaptures
    {
        readonly Dictionary<string, NativePointerCapture> captures = new Dictionary<string, NativePointerCapture>();
        readonly Queue<string> order = new Queue<string>();
        readonly int capacity;
        internal NativePointerCaptures(int capacity = 64)
        {
            if (capacity < 1)
                throw new ArgumentOutOfRangeException("capacity");
            this.capacity = capacity;
        }

        internal void Add(string id, Dictionary<string, object> binding, NativeCaptureGeometry image, NativeCaptureGeometry canvas, string layout, NativeCaptureGeometry[] displays, NativeCaptureGeometry windowBounds = null)
        {
            if (binding["capture"] != null || binding["node"] != null)
                throw new Failure("invalid_request", "capture retention requires a new window or desktop read binding");
            var target = new Dictionary<string, object>(binding);
            target["capture"] = id;
            Retain(new NativePointerCapture(target, image, canvas, layout, displays, windowBounds));
        }

        internal void Retain(NativePointerCapture capture)
        {
            string id = (string)capture.Binding["capture"];
            if (captures.ContainsKey(id))
                throw new Failure("invalid_request", "capture token is already retained");
            captures.Add(id, capture);
            order.Enqueue(id);
            while (order.Count > capacity)
                captures.Remove(order.Dequeue());
        }

        internal object Checkpoint(string id)
        {
            NativePointerCapture capture;
            return captures.TryGetValue(id, out capture) ? capture.Checkpoint() : null;
        }

        internal object[] Checkpoints(Func<NativePointerCapture, bool> retain)
        {
            var result = new List<object>();
            int count = order.Count;
            for (int index = 0; index < count; index++)
            {
                string id = order.Dequeue();
                var capture = captures[id];
                if (retain(capture))
                {
                    order.Enqueue(id);
                    result.Add(capture.Checkpoint());
                }
                else
                    captures.Remove(id);
            }
            return result.ToArray();
        }

        internal NativePointerCapture Get(Dictionary<string, object> target)
        {
            NativePointerCapture capture;
            if (target["capture"] == null || !captures.TryGetValue((string)target["capture"], out capture))
                throw new Failure("snapshot_expired", "the original image has expired; read and view a new image");
            if (!NativeWriteOperation.Same(capture.Binding, target))
                throw new Failure("stale_target", "the image belongs to another window or input scope");
            return capture;
        }
    }
}
