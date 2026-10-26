using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal sealed class NativeClickOffer
    {
        internal readonly string RouteFamily, Interaction, Delivery, Admission, Reason, PreferenceProfile;
        internal readonly string[] PreparationOptions;
        internal NativeClickOffer(string family, string interaction, string delivery, string admission, string reason, string[] preparationOptions, string preferenceProfile = "default")
        {
            RouteFamily = family;
            Interaction = interaction;
            Delivery = delivery;
            Admission = admission;
            Reason = reason;
            PreparationOptions = preparationOptions;
            PreferenceProfile = preferenceProfile;
        }

        internal Dictionary<string, object> WireValue()
        {
            return Wire.Obj("routeFamily", RouteFamily, "interaction", Interaction, "delivery", Delivery, "admission", Admission, "reason", Reason, "preparationOptions", PreparationOptions, "preferenceProfile", PreferenceProfile);
        }
    }
}
