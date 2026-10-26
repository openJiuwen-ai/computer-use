using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace ComputerUse.Windows
{
    // Rendering helpers for the generated SVG. No application or input state.
    internal static class AgentPointerArtwork
    {
        internal static RectangleF Bounds
        {
            get
            {
                float radius = (float)(AgentPointerAssets.RippleToRadius + AgentPointerAssets.RippleWidth / 2);
                var artwork = new RectangleF(-AgentPointerAssets.Hotspot.X, -AgentPointerAssets.Hotspot.Y, AgentPointerAssets.Size.Width, AgentPointerAssets.Size.Height);
                var bounds = RectangleF.Union(artwork, new RectangleF(-radius, -radius, radius * 2, radius * 2));
                bounds.Inflate(1, 1); // Leave room for fractional-pixel antialiasing.
                return bounds;
            }
        }

        internal static Bitmap Gradient(SizeF size, bool radial, double[] points, double[] offsets, Color[] colors)
        {
            const int resolution = 4;
            var image = new Bitmap((int)size.Width * resolution, (int)size.Height * resolution, PixelFormat.Format32bppArgb);
            for (int y = 0; y < image.Height; y++)
                for (int x = 0; x < image.Width; x++)
                {
                    double px = (x + .5) / resolution - points[0], py = (y + .5) / resolution - points[1];
                    double dx = radial ? 0 : points[2] - points[0], dy = radial ? 0 : points[3] - points[1];
                    double at = Math.Max(0, Math.Min(1, radial ? Math.Sqrt(px * px + py * py) / points[2] : (px * dx + py * dy) / (dx * dx + dy * dy)));
                    int i = 0;
                    while (i < offsets.Length - 2 && at > offsets[i + 1])
                        i++;
                    double f = offsets[i + 1] == offsets[i] ? 1 : (at - offsets[i]) / (offsets[i + 1] - offsets[i]);
                    Color a = colors[i], b = colors[i + 1];
                    image.SetPixel(x, y, Color.FromArgb(Mix(a.A, b.A, f), Mix(a.R, b.R, f), Mix(a.G, b.G, f), Mix(a.B, b.B, f)));
                }

            return image;
        }

        static int Mix(int a, int b, double f)
        {
            return Math.Max(0, Math.Min(255, (int)Math.Round(a + (b - a) * f)));
        }

        internal static void Shadow(Graphics graphics, GraphicsPath path, float dx, float dy, float radius, Color color)
        {
            // Small, bounded soft shadow; the shared SVG supplies all geometry
            // and colors. GDI+ has no SVG feDropShadow filter.
            for (int i = 0; i < 5; i++)
            {
                var state = graphics.Save();
                float x = i == 1 ? -radius : i == 2 ? radius : 0;
                float y = i == 3 ? -radius : i == 4 ? radius : 0;
                graphics.TranslateTransform(dx + x * .6f, dy + y * .6f);
                using (var brush = new SolidBrush(Color.FromArgb(color.A / 5, color)))
                    graphics.FillPath(brush, path);
                graphics.Restore(state);
            }
        }
    }

    internal sealed class AgentPointerVisualState
    {
        bool active;
        double idleSince, opacity = 1, opacityFrom = 1, opacityTo = 1, opacityAt, opacityDuration;
        internal void Show(double now)
        {
            idleSince = now;
        }

        internal void Begin(double now)
        {
            active = true;
            Fade(now, 1, AgentPointerAssets.IdleWake);
        }

        internal void End(double now)
        {
            active = false;
            idleSince = now;
        }

        void Fade(double now, double to, double duration)
        {
            if (opacityTo == to)
                return;
            opacityFrom = Value(now);
            opacityTo = to;
            opacityAt = now;
            opacityDuration = duration;
        }

        internal double Value(double now)
        {
            double fraction = opacityDuration <= 0 ? 1 : Math.Max(0, Math.Min(1, (now - opacityAt) / opacityDuration));
            opacity = opacityFrom + (opacityTo - opacityFrom) * fraction;
            return opacity;
        }

        internal double Opacity(double now)
        {
            if (!active && now - idleSince >= AgentPointerAssets.IdleDelay)
                Fade(now, AgentPointerAssets.IdleOpacity, AgentPointerAssets.IdleFade);
            return Value(now);
        }

        internal static double MoveDuration(PointF from, PointF to, bool dragging)
        {
            double x = from.X - to.X, y = from.Y - to.Y;
            return Math.Max(dragging ? AgentPointerAssets.DragMin : AgentPointerAssets.MoveMin, Math.Min(dragging ? AgentPointerAssets.DragMax : AgentPointerAssets.MoveMax, Math.Sqrt(x * x + y * y) / (dragging ? AgentPointerAssets.DragPointsPerSecond : AgentPointerAssets.MovePointsPerSecond)));
        }

        internal static double Ease(double fraction)
        {
            var c = AgentPointerAssets.MoveEasing;
            double lo = 0, hi = 1, t = fraction;
            for (int i = 0; i < 16; i++)
            {
                double x = Bezier(t, c[0], c[2]);
                if (x < fraction)
                    lo = t;
                else
                    hi = t;
                t = (lo + hi) / 2;
            }

            return fraction <= 0 ? 0 : fraction >= 1 ? 1 : Bezier(t, c[1], c[3]);
        }

        static double Bezier(double t, double a, double b)
        {
            return 3 * (1 - t) * (1 - t) * t * a + 3 * (1 - t) * t * t * b + t * t * t;
        }

        internal static double Press(double fraction, int count)
        {
            var times = count == 2 ? AgentPointerAssets.PressDoubleTimes : AgentPointerAssets.PressTimes;
            var scales = count == 2 ? AgentPointerAssets.PressDoubleScales : AgentPointerAssets.PressScales;
            if (fraction <= 0 || fraction >= 1)
                return 1;
            int i = 0;
            while (fraction > times[i + 1])
                i++;
            return scales[i] + (scales[i + 1] - scales[i]) * Ease((fraction - times[i]) / (times[i + 1] - times[i]));
        }
    }
}
