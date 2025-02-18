FROM --platform=linux/arm64 continuumio/miniconda3

WORKDIR /app

COPY environment.yml .
RUN conda env create -f environment.yml

# Make RUN commands use the new environment
SHELL ["conda", "run", "-n", "audioforma-stems", "/bin/bash", "-c"]

COPY . .

EXPOSE 8000

CMD ["conda", "run", "-n", "audioforma-stems", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]