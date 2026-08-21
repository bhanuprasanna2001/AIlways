import VaultDetailContent from "@/components/vaults/vault-detail-content";

export default async function VaultDetailPage({
  params,
}: {
  params: Promise<{ vaultId: string }>;
}) {
  const { vaultId } = await params;
  return <VaultDetailContent vaultId={vaultId} />;
}
