// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class SelectCharacterDeliveryPass
    {
        internal const string Effects = "QUERY";
        internal static NativeTextDelivery Run(INativeCharacterTextSequence sequence, Request request)
        {
            request.Check();
            var delivery = sequence.SelectTextDelivery(request);
            request.Check();
            return delivery;
        }
    }
}
