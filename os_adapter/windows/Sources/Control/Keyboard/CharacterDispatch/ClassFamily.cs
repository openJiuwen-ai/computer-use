// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Text.RegularExpressions;

namespace ComputerUse.Windows
{
    // Control-family evidence, not a claim that managed/native WndProc cannot
    // override character handling. Unknown framework/class versions stay old-route.
    internal sealed class NativeCharacterClassEvidence
    {
        internal string NativeClass, RealClass, Framework;
        internal bool Unicode;
    }
    internal sealed class NativeCharacterClassFamily
    {
        internal readonly string Kind, NativeClass, RealClass, Framework;
        internal NativeCharacterClassFamily(string kind, NativeCharacterClassEvidence evidence)
        {
            Kind = kind;
            NativeClass = evidence.NativeClass;
            RealClass = evidence.RealClass;
            Framework = evidence.Framework;
        }
    }
    internal sealed class NativeCharacterClassResolution
    {
        internal NativeCharacterClassFamily Family;
        internal string Reason;
        internal Exception Error;
    }
    internal static class NativeCharacterClasses
    {
        // NativeWindow.WindowClass.GetFullClassName: supported WinForms 10
        // EDIT family only, not an arbitrary prefix or RichEdit/Combo family.
        static readonly Regex WinFormsEdit =
            new Regex(@"\AWindowsForms10\.EDIT\.app\.[0-9]+\.[0-9a-f]+_r[0-9]+_ad[0-9]+\z",
                      RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        internal static NativeCharacterClassResolution Resolve(NativeCharacterClassEvidence evidence)
        {
            string reason;
            if (evidence == null)
                reason = "native class evidence unavailable";
            else if (!evidence.Unicode)
                reason = "native recipient is not a Unicode window";
            else if (String.IsNullOrEmpty(evidence.NativeClass) || String.IsNullOrEmpty(evidence.RealClass) ||
                     evidence.NativeClass != evidence.RealClass)
                reason = "native class and real class do not agree";
            else if (String.Equals(evidence.NativeClass, "Edit", StringComparison.OrdinalIgnoreCase) &&
                     evidence.Framework == "Win32")
                return new NativeCharacterClassResolution { Family =
                                                                new NativeCharacterClassFamily("win32Edit", evidence),
                                                            Reason = "confirmed Unicode Win32 Edit family" };
            else if (WinFormsEdit.IsMatch(evidence.NativeClass) && evidence.Framework == "WinForm")
                return new NativeCharacterClassResolution {
                    Family = new NativeCharacterClassFamily("winFormsEdit", evidence),
                    Reason = "confirmed Unicode WinForms EDIT family; application character handling retained"
                };
            else
                reason = "unsupported or unknown native class/framework family";
            return new NativeCharacterClassResolution { Reason = reason };
        }
        // One optional initial query, no retry. The adapter preserves explicit
        // object-unavailable errors; caller cancellation/deadline propagate here.
        internal static NativeCharacterClassResolution Read(Func<NativeCharacterClassEvidence> query, Request request)
        {
            try
            {
                request.Check();
                var evidence = query();
                request.Check();
                return Resolve(evidence);
            }
            catch (Exception error)
            {
                request.Check();
                return new NativeCharacterClassResolution { Error = error,
                                                            Reason = "initial class/framework query failed: " +
                                                                     error.GetType().Name + ": " + error.Message };
            }
        }
        internal static void RequireCurrent(NativeCharacterClassFamily original, NativeCharacterClassEvidence current)
        {
            if (original == null || current == null || !current.Unicode ||
                original.NativeClass != current.NativeClass || original.RealClass != current.RealClass ||
                original.Framework != current.Framework)
                throw new Failure("input_unavailable",
                                  "the original character recipient class/framework changed; no alternate route used");
        }
        internal static void RequireNative(NativeCharacterClassFamily original, NativeCharacterClassEvidence current)
        {
            if (original == null || current == null || !current.Unicode ||
                original.NativeClass != current.NativeClass || original.RealClass != current.RealClass)
                throw new Failure("input_unavailable",
                                  "the original character HWND class changed; no alternate route used");
        }
    }
}
