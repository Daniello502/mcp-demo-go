# Docker Hub Setup Guide

This guide explains how to set up Docker Hub authentication for the MCP Kubernetes Demo.

## Why Personal Access Token (PAT)?

Docker Hub no longer allows password authentication for pushing images. You must use a Personal Access Token (PAT) instead.

## Step 1: Create a Personal Access Token

1. Go to [Docker Hub Security Settings](https://hub.docker.com/settings/security)
2. Click "New Access Token"
3. Give it a name (e.g., "MCP Demo Token")
4. Set permissions to "Read, Write, Delete" (or at least "Read, Write")
5. Click "Generate"
6. **Copy the token immediately** - you won't be able to see it again!

## Step 2: Login with Podman

```bash
# Login to Docker Hub
podman login docker.io

# When prompted:
# Username: your-dockerhub-username
# Password: your-personal-access-token (NOT your Docker Hub password)
```

## Step 3: Test Authentication

```bash
# Test that you can push (this will fail if not authenticated)
podman push docker.io/yourusername/test:latest
```

## Step 4: Update Configuration

Update your `.env` file with your Docker Hub username:

```bash
# In your .env file
DOCKER_USERNAME=your-actual-dockerhub-username
```

## Troubleshooting

### "Authentication Required" Error
- Make sure you're using the PAT as the password, not your Docker Hub password
- Verify the token has the correct permissions
- Try logging out and back in: `podman logout docker.io` then `podman login docker.io`

### "Access Denied" Error
- Check that your Docker Hub username is correct
- Verify the image name matches your Docker Hub username
- Ensure the PAT has "Write" permissions

### Token Expired
- Create a new PAT if the old one expired
- Update your login credentials

## Security Best Practices

1. **Use specific tokens**: Create tokens with minimal required permissions
2. **Set expiration**: Set reasonable expiration dates for tokens
3. **Rotate regularly**: Create new tokens periodically
4. **Don't commit tokens**: Never put tokens in version control
5. **Use environment variables**: Store tokens in environment variables when possible

## Alternative: Using Environment Variables

You can also set credentials as environment variables:

```bash
export DOCKER_USERNAME=your-username
export DOCKER_PASSWORD=your-pat-token

# Then login without prompts
echo $DOCKER_PASSWORD | podman login docker.io --username $DOCKER_USERNAME --password-stdin
```

## Verification

After setup, you should be able to:

1. Login successfully: `podman login docker.io`
2. Build the image: `podman build -t docker.io/yourusername/mcp-k8s-server:v1 .`
3. Push the image: `podman push docker.io/yourusername/mcp-k8s-server:v1`

If all steps work, you're ready to deploy the MCP Kubernetes Demo!
