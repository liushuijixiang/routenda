import RequirementUpdateSurvey from "../../../../components/RequirementUpdateSurvey";

export default async function RequirementUpdate({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main>
      <h1>快速更新</h1>
      <div className="card">
        <RequirementUpdateSurvey requirementId={id} />
      </div>
    </main>
  );
}
