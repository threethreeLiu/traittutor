"use client";

import { useEffect, useRef, useState } from "react";

export function useMeasuredHeight<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [height, setHeight] = useState(0);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;

    const updateHeight = () =>
      setHeight(element.getBoundingClientRect().height);
    updateHeight();

    const observer = new ResizeObserver(() => updateHeight());
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return { ref, height };
}
