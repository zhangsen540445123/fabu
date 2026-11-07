import { useCallback, useEffect, useRef, useState } from "react";
import {
  getPublishingReadiness,
  normalizePublishingReadiness,
  unavailableReadiness,
} from "./api";

// Independent of task/config loading: missing endpoints and older mocks fail closed.
export function usePublishingReadiness(active = true) {
  const [readiness, setReadiness] = useState(unavailableReadiness);
  const generation = useRef(0);
  const refreshReadiness = useCallback(async () => {
    const requestId = ++generation.current;
    let next = unavailableReadiness;
    try {
      next = normalizePublishingReadiness(await getPublishingReadiness());
    } catch {
      /* fail closed */
    }
    if (generation.current === requestId) setReadiness(next);
  }, []);
  useEffect(() => {
    if (!active) return;
    void refreshReadiness();
    const timer = setInterval(() => void refreshReadiness(), 2000);
    return () => {
      generation.current++;
      clearInterval(timer);
    };
  }, [active, refreshReadiness]);
  return { readiness, refreshReadiness };
}
