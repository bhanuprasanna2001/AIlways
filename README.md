# AIlways - Meeting Truth & Context Copilot

1. Frontend - Next.js app
2. Backend - Python & FastAPI app


## Decisions

#### Decision 1: The choice of dataset to build AIlways.

The dataset that I chose for building AIlways:

1. Company Documents Dataset - Consists of 2,677 documents in pdf format. It has inventory reports, invoices, purchase orders, and shipping orders.
   1. **Why I chose it:**
      1. The dataset is relevant to the use case of AIlways, which is to help employees find information from company documents.

I went over multiple datasets:

1. OpenRAG_Bench - Consists of 1000 arxiv papers which are extracted into json format. It has text, tables, and figures.
   1. Why I didn't choose it:
      1. The whole dataset is in JSON format
2. Enron Email Dataset - Consists of 500k emails from Enron employees. It has text and attachments.
   1. Why I didn't choose it:
      1. The dataset is about fraudulent activities in Enron, which is not relevant to the use case of AIlways.
3. And a few more datasets that I found on Kaggle and Hugging Face, but they were either too small or not relevant to the use case of AIlways.


#### Decision 2: The choice of document parsing library.



## Frontend

To run the frontend, navigate to the `frontend` folder and run:

```bash
cd frontend

npm install
npm run dev
```

## Backend

To run the backend, navigate to the `backend` folder and run:

```bash
cd backend

docker compose up -d

uv sync
uv run python -m app
```

## Contributing

If you want to contribute to this project, please fork the repository and create a pull request with your changes. We welcome contributions of all kinds, including bug fixes, new features, and documentation improvements.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more details.