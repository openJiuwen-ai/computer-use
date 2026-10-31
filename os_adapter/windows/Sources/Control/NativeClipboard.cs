// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Text;
using System.Threading;

namespace ComputerUse.Windows
{
    internal sealed class NativeClipboardData
    {
        internal readonly uint Format;
        internal readonly byte[] Bytes;
        internal NativeClipboardData(uint format, byte[] bytes)
        {
            Format = format;
            Bytes = bytes;
        }
    }

    internal interface INativeClipboardPayload : IDisposable
    {
    }

    // All version checks and replacements take place under the native clipboard
    // lock. Payloads allocate every required representation before any clear.
    internal interface INativeClipboardApi : IDisposable
    {
        IDisposable Open(Request request);
        uint Version { get; }

        IList<NativeClipboardData> Read(Request request);
        INativeClipboardPayload Prepare(IList<NativeClipboardData> entries, Request request);
        INativeClipboardPayload PrepareText(string text, NativePasteConsumer consumer, Request request);
        void Replace(INativeClipboardPayload payload, Action changed, Request request);
        bool IsCurrentPublication(uint installedVersion);
        NativePasteHandoff Handoff(Request request);
    }

    internal interface INativeClipboardProvider
    {
        NativeClipboardTransaction Save(Request request);
    }

    internal enum NativeClipboardRestore
    {
        Restored,
        OwnershipLost,
        Failed
    }

    internal static class NativeClipboardText
    {
        internal static IList<NativeClipboardData> Formats(string text, uint locale, Encoding ansi, Encoding oem)
        {
            // CloseClipboard synthesizes missing text formats and changes the
            // sequence. Publish this complete family before recording ownership.
            // The three Windows text formats require CRLF line endings.
            // Normalize only the published payload, never captured document
            // text or the original clipboard entries saved for restoration.
            text = text.Replace("\r\n", "\n").Replace("\r", "\n").Replace("\n", "\r\n");
            return new[] { new NativeClipboardData(13, Encoding.Unicode.GetBytes(text + "\0")),
                           new NativeClipboardData(1, ansi.GetBytes(text + "\0")),
                           new NativeClipboardData(7, oem.GetBytes(text + "\0")),
                           new NativeClipboardData(16, BitConverter.GetBytes(locale)) };
        }
    }

    internal sealed class NativeClipboardTransaction
    {
        readonly INativeClipboardApi api;
        readonly uint initial;
        readonly INativeClipboardPayload original;
        readonly double deadline;
        uint? owned;
        bool finished;
        bool rolledBack, rollbackFailed;
        bool handoffCompleted;
        NativeClipboardRestore finishResult;
        internal bool Prepared { get; private set; }

        internal NativeClipboardTransaction(INativeClipboardApi api, Request r)
        {
            this.api = api;
            deadline = r.Deadline;
            try
            {
                IList<NativeClipboardData> entries;
                using (api.Open(r))
                {
                    entries = api.Read(r);
                    // Delayed rendering may legitimately advance the version
                    // while materializing old formats under this same lock.
                    initial = api.Version;
                    // A fresh empty clipboard can have sequence zero. Only
                    // accept that initial state after successful locked format
                    // enumeration; nonempty data still needs version tracking.
                    if (initial == 0 && entries.Count != 0)
                        throw new Failure("input_unavailable",
                                          "clipboard version is unavailable for existing data; no paste sent");
                }

                original = api.Prepare(entries, r);
            }
            catch
            {
                try
                {
                    api.Dispose();
                }
                catch (Exception cleanup)
                {
                    throw new NativeClipboardCleanupFailure(
                        "clipboard initialization failed and resource cleanup could not be confirmed: " +
                        cleanup.Message);
                }
                throw;
            }
        }
        internal void Install(string text, Request r, bool finalParagraph = false, NativePasteConsumer consumer = null)
        {

            if (Prepared || finished)
                throw new Failure("stale_target", "clipboard transaction has already been used");
            NativePaste.Validate(text);
            if (finalParagraph && (text.Length == 0 || text[text.Length - 1] != '\r' && text[text.Length - 1] != '\n'))
                throw new Failure("invalid_request",
                                  "a native final paragraph marker requires a trailing text paragraph");
            // Native rich-text mode consumes one final paragraph as its fragment
            // terminator. Keep Unicode plain-text font binding and the normal
            // paste chord; never import RTF or change the receiver's edit mode.
            // A CRLF suffix also keeps an original lone CR from merging with it.
            using (var payload =
                       api.PrepareText(finalParagraph ? text + "\r\n" : text, consumer, r)) using (api.Open(r))
            {
                if (api.Version != initial)
                    throw new Failure("input_unavailable", "clipboard changed before paste; no paste sent");
                if (initial == 0 && api.Read(r).Count != 0)
                    throw new Failure("input_unavailable",
                                      "the initially empty clipboard now contains data; no paste sent");
                try
                {
                    api.Replace(payload,
                                () =>
                                {
                                    Prepared = true;
                                    r.Effect = true;
                                },
                                r);
                    owned = api.Version;
                    if (owned.Value == 0)
                        throw new Failure(
                            "input_unavailable",
                            "clipboard publication has no trackable version; restored the previous contents without pasting");
                }
                catch
                {
                    // A partial text family would change again on close. Roll
                    // back while still holding the original lock, independently
                    // of cancellation; never adopt a later outside revision.
                    if (Prepared)
                    {
                        try
                        {
                            api.Replace(original,
                                        () =>
                                        {},
                                        new Request(CancellationToken.None, 400));
                            rolledBack = true;
                        }
                        catch
                        {
                            rollbackFailed = true;
                        }
                    }

                    throw;
                }
            }
        }

        internal void Validate(Request r)
        {
            if (!Prepared || finished || rolledBack || rollbackFailed || !owned.HasValue)
                throw new Failure("stale_target", "clipboard payload is not available for this paste");
            using (api.Open(r))
            {
                if (!api.IsCurrentPublication(owned.Value))
                    throw new Failure("input_unavailable",
                                      "clipboard changed before the paste chord; no further input sent");
            }
        }
        internal NativePasteHandoff Handoff(Request r)
        {
            var delivery = api.Handoff(r);
            handoffCompleted = true;
            return delivery;
        }

        internal NativeClipboardRestore Finish(bool afterInput)
        {
            if (finished)
                return finishResult;
            finished = true;
            var result = rollbackFailed ? NativeClipboardRestore.Failed : NativeClipboardRestore.Restored;
            // A partial chord may have started a paste. Await its evidence once
            // without re-sending, even when dispatch threw before normal wait.
            if (Prepared && afterInput && !handoffCompleted)
            {
                try
                {
                    Handoff(new Request(CancellationToken.None, Math.Max(1, (int)Math.Min(2500, deadline - Wire.Now))));
                }
                catch (Failure error)
                {
                    if (error.Code != "cancelled" && error.Code != "deadline_exceeded")
                        result = NativeClipboardRestore.Failed;
                }
                catch
                {
                    result = NativeClipboardRestore.Failed;
                }
            }

            try
            {
                if (Prepared && !rolledBack && !rollbackFailed)
                {
                    using (api.Open(new Request(CancellationToken.None, 600)))
                    {
                        if (!owned.HasValue)
                            result = NativeClipboardRestore.Failed;
                        else if (!api.IsCurrentPublication(owned.Value))
                        {
                            if (result != NativeClipboardRestore.Failed)
                                result = NativeClipboardRestore.OwnershipLost;
                        }
                        else
                            api.Replace(original,
                                        () =>
                                        {},
                                        new Request(CancellationToken.None, 400));
                    }
                }
            }
            catch
            {
                result = NativeClipboardRestore.Failed;
            }

            try
            {
                original.Dispose();
            }
            catch
            {
                result = NativeClipboardRestore.Failed;
            }

            try
            {
                api.Dispose();
            }
            catch
            {
                result = NativeClipboardRestore.Failed;
            }

            finishResult = result;
            return result;
        }
    }
}
