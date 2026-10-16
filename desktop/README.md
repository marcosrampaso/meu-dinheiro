# Aplicativo macOS — prévia local

O aplicativo abre a interface em uma janela nativa WebKit e inicia seu próprio backend Python em `127.0.0.1`, numa porta livre. Ao fechar a última janela, o serviço local é encerrado. Não exige instalar Python ou Xcode para usar o pacote pronto.

## Usar

1. Abra o DMG em `dist/Meu-Dinheiro-macOS-arm64.dmg`.
2. Arraste **Meu Dinheiro.app** para **Applications**.
3. Abra o aplicativo e escolha **Criar minha conta local** ou **Explorar demonstração**.

Esta compilação é para **Apple Silicon e macOS 26 ou superior**, conforme o runtime Python usado na geração. A janela nativa foi verificada no Mac de desenvolvimento; versões anteriores precisam de outra compilação com um runtime compatível.

O pacote tem assinatura **ad hoc**, para testes locais. Ainda não possui assinatura Developer ID nem notarização da Apple. Portanto, esta prévia não é uma distribuição pública validada pelo Gatekeeper. Para uma versão pública, assine os componentes e o aplicativo com Developer ID, notarize e anexe o comprovante antes de distribuir. Não é necessário desativar as proteções do macOS.

## Dados

Por padrão, a pasta é `~/Library/Application Support/Meu Dinheiro`. **O instalador não contém bancos, extratos, tokens ou o histórico do desenvolvedor.** Uma atualização do `.app` não substitui essa pasta.

O menu **Meu Dinheiro → Abrir pasta dos dados** abre a pasta atual. **Usar outra pasta de dados…** permite escolher uma pasta existente. Para usar o histórico do projeto, feche primeiro o servidor iniciado no terminal e selecione a pasta `data` do projeto. Não selecione apenas o arquivo `.db`: as credenciais e identidades também pertencem à pasta. O app usa os arquivos no lugar, sem copiá-los para o instalador.

Só uma instância pode usar a mesma pasta por vez. Faça backup da pasta inteira com o app fechado. As senhas são guardadas como hashes scrypt. Os arquivos financeiros continuam sendo SQLite local, **sem criptografia integral do banco**; a separação de contas protege o acesso pela aplicação, não contra alguém que controla a mesma conta do sistema operacional ou lê os arquivos diretamente. Use a proteção de disco e de sessão do próprio macOS.

## Compilar

Num Mac Apple Silicon, com Xcode Command Line Tools:

```sh
.venv/bin/python -m pip install -r requirements-desktop.txt
.venv/bin/python scripts/build_macos.py
```

O script empacota o backend com PyInstaller, o leitor de PDF vendorizado, certificados HTTPS e o helper de Touch ID pré-compilado. Compila a janela nativa, inclui licenças das dependências e gera o DMG. Usa uma lista explícita de recursos públicos; nunca inclui a pasta `data`. `build/` e `dist/` ficam fora do Git.

Os ambientes `MEU_DINHEIRO_DATA_DIR` e `MEU_DINHEIRO_SMOKE_TEST=1` são usados pelo teste de inicialização com uma pasta temporária. Não removem autenticação nem dão acesso às contas existentes.
