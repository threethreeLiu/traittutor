"use client";

import { useTranslation } from "react-i18next";

import { ServiceConfigEditor } from "@/components/settings/ServiceConfigEditor";
import { SettingsPageHeader } from "@/components/settings/shared";

export default function VideoGenSettingsPage() {
  const { t } = useTranslation();
  return (
    <div>
      <SettingsPageHeader
        title={t("Video Generation")}
        description={t(
          "Generate short source-grounded learning videos. The active default uses Agnes Video 2.0 and safely falls back to the text lesson when the shared video queue is unavailable.",
        )}
      />
      <ServiceConfigEditor service="videogen" />
    </div>
  );
}
