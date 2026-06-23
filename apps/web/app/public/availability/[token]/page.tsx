import PublicAvailabilityForm from "../../../../components/PublicAvailabilityForm";

export default async function PublicAvailability({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  return (
    <main className="public-shell">
      <h1>选择可拜访时间</h1>
      <PublicAvailabilityForm token={token} />
    </main>
  );
}
