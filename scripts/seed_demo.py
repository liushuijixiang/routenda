from visit_agent.infrastructure.db.repository import InMemoryRepository, seed_demo


if __name__ == "__main__":
    repo = seed_demo(InMemoryRepository())
    print(repo.snapshot_counts())
