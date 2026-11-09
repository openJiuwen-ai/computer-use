// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    // Compiled into an isolated copy of the capture worker for this probe only.
    internal static class OwnedCaptureGate
    {
        [DllImport("user32.dll")]
        static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")]
        static extern IntPtr GetAncestor(IntPtr window, uint flags);
        [DllImport("user32.dll")]
        static extern IntPtr GetWindow(IntPtr window, uint command);
        [DllImport("user32.dll")]
        static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern IntPtr GetProp(IntPtr window, string name);

        static readonly string Directory = Path.GetDirectoryName(typeof(OwnedCaptureGate).Assembly.Location);

        internal static void Require(Dictionary<string, object> request)
        {
            var allowed = Wire.Object(Wire.Serializer().DeserializeObject(File.ReadAllText(Path.Combine(Directory, "fixture.json"))));
            var window = new IntPtr(Convert.ToInt64(request["handle"]));
            var caption = new StringBuilder(256);
            uint pid;
            string route = request["route"] as string;
            if ((route != "wgc" && route != "print") || window == IntPtr.Zero || window.ToInt64() != Convert.ToInt64(allowed["hwnd"]) ||
                Convert.ToUInt32(request["pid"]) != Convert.ToUInt32(allowed["pid"]) ||
                !Equals(request["started"], allowed["started"]) || !IsWindowVisible(window) ||
                GetAncestor(window, 2) != window || GetWindow(window, 4) != IntPtr.Zero ||
                GetWindowThreadProcessId(window, out pid) == 0 || pid != Convert.ToUInt32(allowed["pid"]) ||
                GetProp(window, (string)allowed["property"]) != new IntPtr(1) ||
                GetWindowText(window, caption, caption.Capacity) == 0 || caption.ToString() != (string)allowed["title"])
                throw new InvalidOperationException("the synthetic fixture capture gate refused this window");
            using (var process = Process.GetProcessById((int)pid))
                if (process.StartTime.ToUniversalTime().Ticks.ToString() != (string)allowed["started"])
                    throw new InvalidOperationException("the synthetic fixture process lifetime changed");
        }

        internal static void VerifyPixels(Dictionary<string, object> request, Bitmap bitmap)
        {
            Require(request);
            VerifyPalette(bitmap);
            File.AppendAllText(Path.Combine(Directory, "accepted.jsonl"), Wire.Json(Wire.Obj(
                "route", request["route"], "hwnd", request["handle"], "pid", request["pid"], "started", request["started"]
            )) + Environment.NewLine);
        }

        internal static void VerifyPalette(Bitmap bitmap)
        {
            if (bitmap.Width > 840 || bitmap.Height > 430)
                throw new InvalidOperationException("the synthetic window image has an unexpected size");
            var colors = new HashSet<int> { 0, Color.Black.ToArgb(), Color.FromArgb(7, 11, 17).ToArgb() };
            for (int row = 0; row < 5; row++)
                for (int col = 0; col < 10; col++)
                    colors.Add(Color.FromArgb(37 + row * 31, 29 + col * 17, 239 - row * 31 - col * 7).ToArgb());
            for (int y = 0; y < bitmap.Height; y++)
                for (int x = 0; x < bitmap.Width; x++)
                    if (!colors.Contains(bitmap.GetPixel(x, y).ToArgb()))
                        throw new InvalidOperationException("the image contains pixels outside the synthetic fixture palette");
        }
    }
}
