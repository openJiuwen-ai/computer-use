using System;
using System.Linq;
using System.Text;

namespace ComputerUse.Windows
{
    internal static class NativeClipboardFormatsTests
    {
        static void Main()
        {
            int assertions = 0;
            foreach (string separator in new[]
            {
                "\n",
                "\r\n",
                "\r"
            }

            )
            {
                string original = "first" + separator + "second 🙂" + separator;
                var entries = NativeClipboardText.Formats(original, 1033, Encoding.UTF8, Encoding.UTF8);
                string expected = "first\r\nsecond 🙂\r\n\0";
                foreach (uint format in new uint[]
                {
                    13,
                    1,
                    7
                }

                )
                {
                    var bytes = entries.Single(entry => entry.Format == format).Bytes;
                    string actual = (format == 13 ? Encoding.Unicode : Encoding.UTF8).GetString(bytes);
                    assertions++;
                    if (actual != expected)
                        throw new InvalidOperationException("clipboard text family has non-CRLF line endings");
                }

                assertions++;
                if (original != "first" + separator + "second 🙂" + separator)
                    throw new InvalidOperationException("source changed");
            }

            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "scope", "production clipboard bytes; no UI or clipboard ownership claim")));
        }
    }
}
