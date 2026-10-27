using System;
using System.Drawing;
using System.Drawing.Imaging;

namespace ComputerUse.Windows
{
    internal static class AgentPointerTests
    {
        static int assertions;
        static void Check(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static bool Near(double a, double b)
        {
            return Math.Abs(a - b) < .0001;
        }

        static int Main()
        {
            var clock = new AgentPointerVisualState();
            clock.Show(0);
            Check(Near(clock.Opacity(4.99), 1), "initial five-second visibility");
            Check(Near(clock.Opacity(5), 1), "fade starts from current opacity");
            Check(Near(clock.Opacity(5.15), .7), "idle fade midpoint");
            Check(Near(clock.Opacity(5.3), .4), "idle opacity");
            Check(Near(clock.Opacity(6), .4), "read-only sampling does not wake");
            clock.Begin(6);
            Check(Near(clock.Opacity(6), .4), "wake starts continuously");
            Check(Near(clock.Opacity(6.05), .7), "wake midpoint");
            Check(Near(clock.Opacity(6.1), 1), "wake finished");
            Check(Near(clock.Opacity(26), 1), "long operation stays active");
            clock.End(26);
            Check(Near(clock.Opacity(30.99), 1), "idle delay begins at completion");
            clock.Opacity(31);
            Check(Near(clock.Opacity(31.3), .4), "completed operation fades");
            clock.Begin(31.31);
            Check(clock.Opacity(31.36) > .6, "interrupt idle fade without jump");
            Check(Near(AgentPointerVisualState.MoveDuration(PointF.Empty, PointF.Empty, false), .12), "minimum movement duration");
            Check(Near(AgentPointerVisualState.MoveDuration(PointF.Empty, new PointF(900, 0), false), .28), "maximum movement duration");
            Check(Near(AgentPointerVisualState.MoveDuration(PointF.Empty, new PointF(900, 0), true), .08), "drag follows the latest point");
            Check(Near(AgentPointerVisualState.Ease(0), 0) && Near(AgentPointerVisualState.Ease(1), 1), "easing endpoints");
            Check(Near(AgentPointerVisualState.Ease(.5), .5), "shared symmetric easing");
            Check(Near(AgentPointerVisualState.Press(.4, 1), .9), "single press minimum");
            Check(Near(AgentPointerVisualState.Press(.2, 2), .9) && Near(AgentPointerVisualState.Press(.7, 2), .9), "double press");
            Check(Near(AgentPointerVisualState.Press(1, 2), 1), "press ends at original size");
            using (var image = new Bitmap((int)(AgentPointerAssets.Size.Width * 4), (int)(AgentPointerAssets.Size.Height * 4), PixelFormat.Format32bppPArgb))
            {
                using (var graphics = Graphics.FromImage(image))
                {
                    graphics.ScaleTransform(4, 4);
                    AgentPointerAssets.Draw(graphics);
                }

                Check(image.GetPixel(0, 0).A == 0 && image.GetPixel(image.Width - 1, 0).A == 0, "transparent canvas");
                int hx = (int)(AgentPointerAssets.Hotspot.X * 4), hy = (int)(AgentPointerAssets.Hotspot.Y * 4);
                var body = image.GetPixel(hx + 24, hy + 36);
                Check(body.A > 210 && body.R > 200 && body.B >= body.R, "shared porcelain-white artwork");
                var halo = image.GetPixel(64, 144);
                Check(halo.A > 0 && halo.A < 100 && halo.B > halo.R, "soft grey-blue halo outside the arrow");
                Check(halo.A < image.GetPixel(80, 144).A && image.GetPixel(80, 144).A < image.GetPixel(96, 144).A, "radial halo fades outward");
                Check(Math.Abs(halo.A - image.GetPixel(image.Width - 1 - 64, 144).A) <= 2, "circular halo has symmetric opacity");
                using (var shape = AgentPointerAssets.Path())
                {
                    var bounds = shape.GetBounds();
                    Check(bounds.Width > 15 && bounds.Width < 18 && bounds.Height > 18 && bounds.Height < 21, "selected enlarged silhouette dimensions");
                }

                var canvas = AgentPointerArtwork.Bounds;
                using (var host = new Bitmap((int)Math.Ceiling(canvas.Width * 4), (int)Math.Ceiling(canvas.Height * 4), PixelFormat.Format32bppPArgb))
                {
                    using (var graphics = Graphics.FromImage(host))
                    {
                        graphics.ScaleTransform(4, 4);
                        graphics.TranslateTransform(-canvas.Left - AgentPointerAssets.Hotspot.X, -canvas.Top - AgentPointerAssets.Hotspot.Y);
                        AgentPointerAssets.Draw(graphics);
                    }

                    int x0 = (int)Math.Round((-canvas.Left - AgentPointerAssets.Hotspot.X) * 4);
                    int y0 = (int)Math.Round((-canvas.Top - AgentPointerAssets.Hotspot.Y) * 4);
                    bool equal = x0 >= 0 && y0 >= 0 && x0 + image.Width <= host.Width && y0 + image.Height <= host.Height;
                    for (int y = 0; equal && y < image.Height; y++)
                        for (int x = 0; x < image.Width; x++)
                            if (host.GetPixel(x + x0, y + y0).ToArgb() != image.GetPixel(x, y).ToArgb())
                            {
                                equal = false;
                                break;
                            }

                    Check(equal, "host canvas preserves the complete artwork at its hotspot without clipping");
                }
            }

            Console.WriteLine("{\"assertions\":" + assertions + ",\"passed\":true}");
            return 0;
        }
    }
}
