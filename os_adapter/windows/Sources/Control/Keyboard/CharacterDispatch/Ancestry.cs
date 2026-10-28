// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal sealed class NativeCharacterAncestor
    {
        internal string Identity, Role;
        internal bool Within, Root;
    }
    internal static class NativeCharacterAncestry
    {
        internal static bool Clear(Func<NativeCharacterAncestor> next, Request request)
        {
            var seen = new HashSet<string>();
            try
            {
                for (int count = 0; count < 32; count++)
                {
                    request.Check();
                    var ancestor = next();
                    request.Check();
                    if (ancestor == null || !ancestor.Within || String.IsNullOrEmpty(ancestor.Identity) ||
                        String.IsNullOrEmpty(ancestor.Role) || !seen.Add(ancestor.Identity))
                        return false;
                    if (ancestor.Role == "ControlType.ComboBox" || ancestor.Role == "ControlType.Spinner")
                        return false;
                    if (ancestor.Root)
                        return true;
                    if (ancestor.Role != "ControlType.Pane" && ancestor.Role != "ControlType.Group" &&
                        ancestor.Role != "ControlType.Custom")
                        return false;
                }
                return false;
            }
            catch
            {
                request.Check();
                throw;
            }
        }
    }
}
