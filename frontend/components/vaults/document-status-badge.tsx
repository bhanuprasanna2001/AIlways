import { STATUS_LABELS, STATUS_VARIANT } from "@/lib/constants";
import { Badge } from "@/components/ui/badge";
import type { DocumentStatus } from "@/lib/types";

type Props = {
  status: DocumentStatus;
};

export function DocumentStatusBadge({ status }: Props) {
  const label = STATUS_LABELS[status] ?? status;
  const variant = STATUS_VARIANT[status] ?? "neutral";

  return <Badge variant={variant}>{label}</Badge>;
}
