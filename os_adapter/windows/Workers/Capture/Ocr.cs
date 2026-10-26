using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Windows.Foundation;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage.Streams;

namespace ComputerUse.Windows
{
    // Evidence from the exact encoded image, never synthetic UIA elements or
    // input authority. This helper process handles a single capture then exits;
    // an OCR timeout must not discard pixels that were already acquired.
    internal static class CaptureOcr
    {
        static object Unavailable(string reason)
        {
            if (String.IsNullOrWhiteSpace(reason))
                reason = "recognition unavailable";
            int n = Math.Min(reason.Length, 512);
            if (n < reason.Length && n > 0 && Char.IsHighSurrogate(reason[n - 1]))
                n--;
            return Wire.Obj("source", "windowsOCR", "status", "unavailable", "language", null, "lines", new object[0], "truncated", false, "reason", reason.Substring(0, n));
        }

        static T Wait<T>(IAsyncOperation<T> operation)
        {
            try
            {
                var watch = System.Diagnostics.Stopwatch.StartNew();
                while (operation.Status == AsyncStatus.Started && watch.ElapsedMilliseconds < 500)
                    Thread.Sleep(5);
                if (operation.Status != AsyncStatus.Completed)
                {
                    operation.Cancel();
                    throw new TimeoutException("recognition operation unavailable or timed out");
                }

                return operation.GetResults();
            }
            finally
            {
                operation.Close();
            }
        }

        internal static object Read(byte[] png, int width, int height)
        {
            try
            {
                var task = Task.Run(() => Recognize(png, width, height));
                return task.Wait(800) ? task.Result : Unavailable("recognition exceeded the 800 ms budget; pixels remain available");
            }
            catch (Exception error)
            {
                return Unavailable(error.GetBaseException().Message);
            }
        }

        static object Recognize(byte[] png, int width, int height)
        {
            var engine = OcrEngine.TryCreateFromUserProfileLanguages();
            if (engine == null)
                return Unavailable("no recognizer for the installed user languages");
            if (width > OcrEngine.MaxImageDimension || height > OcrEngine.MaxImageDimension)
                return Unavailable("image exceeds the OCR engine's dimension limit; it was not resized");
            using (var stream = new InMemoryRandomAccessStream())
            {
                using (var writer = new DataWriter(stream))
                {
                    writer.WriteBytes(png);
                    Wait(writer.StoreAsync());
                    writer.DetachStream();
                }

                stream.Seek(0);
                var decoder = Wait(BitmapDecoder.CreateAsync(stream));
                using (var bitmap = Wait(decoder.GetSoftwareBitmapAsync()))
                {
                    if (bitmap.PixelWidth != width || bitmap.PixelHeight != height)
                        return Unavailable("decoded image dimensions changed");
                    var result = Wait(engine.RecognizeAsync(bitmap));
                    var lines = new List<object>();
                    bool truncated = false;
                    int length = 0;
                    foreach (var line in result.Lines)
                    {
                        if (lines.Count == 64 || length >= 8192)
                        {
                            truncated = true;
                            break;
                        }

                        if (line.Words.Count == 0 || String.IsNullOrWhiteSpace(line.Text))
                            continue;
                        string text = line.Text;
                        int limit = Math.Min(256, 8192 - length);
                        if (text.Length > limit)
                        {
                            int n = limit;
                            if (n > 0 && Char.IsHighSurrogate(text[n - 1]))
                                n--;
                            text = text.Substring(0, n);
                            truncated = true;
                        }

                        if (String.IsNullOrWhiteSpace(text))
                        {
                            truncated = true;
                            break;
                        }

                        double x = Math.Max(0, line.Words.Min(w => w.BoundingRect.Left)), y = Math.Max(0, line.Words.Min(w => w.BoundingRect.Top));
                        double right = Math.Min(width, line.Words.Max(w => w.BoundingRect.Right)), bottom = Math.Min(height, line.Words.Max(w => w.BoundingRect.Bottom));
                        if (right <= x || bottom <= y)
                        {
                            truncated = true;
                            continue;
                        }

                        lines.Add(Wire.Obj("text", text, "rect", Wire.Obj("x", x, "y", y, "width", right - x, "height", bottom - y)));
                        length += text.Length;
                    }

                    return Wire.Obj("source", "windowsOCR", "status", truncated ? "partial" : "complete", "language", engine.RecognizerLanguage.LanguageTag, "lines", lines, "truncated", truncated);
                }
            }
        }
    }
}
