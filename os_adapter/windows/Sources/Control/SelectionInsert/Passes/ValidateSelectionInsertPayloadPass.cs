// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ValidateSelectionInsertPayloadPass
    {
        internal const string Effects = "PURE";
        internal static void Run(string text)
        {
            if (text == null)
                throw new Failure("invalid_request", "selection insertion requires text");
            NativeText.Validate(text);
        }
    }
}
