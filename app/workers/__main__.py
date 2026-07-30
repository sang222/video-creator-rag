"""Process entrypoint for VCOS durable background workers."""

from app.workers.production_workflow import main


if __name__ == "__main__":
    main()
