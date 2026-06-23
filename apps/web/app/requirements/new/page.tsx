import RequirementWorkspace from "../../../components/RequirementWorkspace";

export default function NewRequirement() {
  return (
    <main className="wide-main">
      <header className="page-header">
        <div>
          <p className="eyebrow">需求录入</p>
          <h1>新建拜访需求</h1>
        </div>
      </header>
      <RequirementWorkspace />
    </main>
  );
}
