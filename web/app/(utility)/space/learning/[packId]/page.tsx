"use client";

import { useParams } from "next/navigation";
import { useTranslation } from "react-i18next";
import LearningCanvas from "@/components/learning/LearningCanvas";

export default function LearningPackRoute() {
  const params = useParams<{ packId: string }>();
  const { i18n } = useTranslation();
  const locale = i18n.language.toLowerCase().startsWith("zh") ? "zh" : "en";
  return <LearningCanvas packId={params.packId} locale={locale} />;
}
